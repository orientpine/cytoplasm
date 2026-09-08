#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo 'Usage: run.sh [--evidence-dir DIR] [--keep] [-- <command...>]'
}

harness_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo=$(git -C "$harness_dir" rev-parse --show-toplevel)
evidence_dir=/tmp/install-systemd-qa
keep=false
while (($#)); do
    case "$1" in
        --evidence-dir)
            if (($# < 2)) || [[ -z $2 ]]; then
                usage >&2
                exit 2
            fi
            evidence_dir=$2
            shift 2
            ;;
        --keep) keep=true; shift ;;
        --) shift; break ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
command=("$@")
default_command=false
if ((${#command[@]} == 0)); then
    default_command=true
    command=(python3 -m automation.install --config /root/node.toml --update-trust-key /root/trust.pub)
fi

umask 077
mkdir -p -- "$evidence_dir"
evidence_dir=$(cd -- "$evidence_dir" && pwd)
image=autophagy-install-qa:systemd
container_name=autophagy-install-qa-$$
# Freeze HEAD once: other workers may advance the branch during a build.
revision=$(git -C "$repo" rev-parse HEAD)
started=$SECONDS
temporary=''
boot_directory=''
container_attempted=false
cleanup() {
    local rc=$?
    trap - EXIT
    if [[ -n $temporary ]]; then
        if ! rm -rf -- "$temporary"; then
            echo 'HARNESS-CLEANUP-FAIL: cannot remove temporary key directory' >&2
            rc=125
        fi
    fi
    if "$container_attempted"; then
        if "$keep"; then
            printf 'Kept container: %s (remove with docker rm -f %s)\n' "$container_name" "$container_name"
        elif ! docker rm -f "$container_name" > /dev/null; then
            echo "HARNESS-CLEANUP-FAIL: cannot remove $container_name" >&2
            rc=125
        fi
    fi
    if [[ -n $boot_directory ]]; then
        if "$keep"; then
            printf 'After removing the kept container: rm -rf -- %q\n' "$boot_directory"
        elif ! rm -rf -- "$boot_directory"; then
            echo 'HARNESS-CLEANUP-FAIL: cannot remove boot notification directory' >&2
            rc=125
        fi
    fi
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'WORKING: build %s\n' "$image"
# Only the Dockerfile enters the build context, never local credentials or .venv.
docker build --tag "$image" - < "$harness_dir/Dockerfile" 2>&1 | tee "$evidence_dir/build-transcript.txt"
image_digest=$(docker image inspect --format '{{.Id}}' "$image")
printf 'WORKING: boot %s\n' "$container_name"
container_attempted=true
boot_directory=$(mktemp -d)
# Bind the READY=1 receiver BEFORE starting PID 1. systemd 255 supports the
# vmm.notify_socket credential; unlike an immediate systemctl call, this cannot
# race creation of /run/systemd/private. No sleeps or readiness polling.
boot_rc=0
python3 - "$container_name" "$image_digest" "$boot_directory" <<'PY' > "$evidence_dir/boot-state.txt" 2>&1 || boot_rc=$?
import socket
import subprocess
import sys
import time
from pathlib import Path

name, image, temporary = sys.argv[1:]
deadline = time.monotonic() + 60

def remaining():
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError('systemd boot exceeded 60 seconds')
    return seconds

try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(str(Path(temporary) / 'notify'))
        subprocess.run([
            'docker', 'run', '-d', '--name', name, '--hostname', name,
            '--privileged', '--cgroupns=host', '--tmpfs', '/run',
            '--tmpfs', '/run/lock', '-v', '/sys/fs/cgroup:/sys/fs/cgroup:rw',
            '--mount', f'type=bind,src={temporary},dst=/run/host', image,
            '/sbin/init', 'systemd.set_credential=vmm.notify_socket:/run/host/notify',
        ], check=True, timeout=remaining())
        while True:
            receiver.settimeout(remaining())
            if b'READY=1' not in receiver.recv(4096).splitlines():
                continue
            result = subprocess.run([
                'docker', 'exec', name, 'systemctl', 'is-system-running', '--wait',
            ], capture_output=True, text=True, timeout=remaining())
            print(result.stdout.strip(), flush=True)
            if result.stdout.strip() not in ('running', 'degraded'):
                raise RuntimeError(f'state rc={result.returncode}: {result.stderr.strip()}')
            break
except (OSError, subprocess.SubprocessError, RuntimeError) as error:
    print(f'SYSTEMD-BOOT-FAIL: {error}', file=sys.stderr)
    raise SystemExit(1)
PY
if ((boot_rc != 0)); then
    printf 'SYSTEMD-BOOT-FAIL: readiness rc=%s (60s limit)\n' "$boot_rc" >&2
    docker logs "$container_name" > "$evidence_dir/systemd-journal.txt" 2>&1
    tail -n 30 "$evidence_dir/boot-state.txt" "$evidence_dir/systemd-journal.txt" >&2
    exit 125
fi
boot_state=$(tail -n 1 "$evidence_dir/boot-state.txt")
# Keep the bind source alive until container removal: Docker's cp endpoint
# reconstructs mounts, and fails if even an unrelated bind source was deleted.
printf 'systemctl is-system-running: %s\n' "$boot_state"
docker exec "$container_name" mkdir -p /root/autophagy-agents
git -C "$repo" archive "$revision" | docker exec -i "$container_name" \
    tar -x -C /root/autophagy-agents --exclude='.omo' --exclude='.venv' --exclude='.env.secrets'

temporary=$(mktemp -d)
ssh-keygen -q -t ed25519 -N '' -C install-systemd-qa -f "$temporary/trust"
docker cp "$temporary/trust.pub" "$container_name:/root/trust.pub"
rm -rf -- "$temporary"
temporary=''

docker exec -i -w /root/autophagy-agents "$container_name" python3 - <<'PY' | tee "$evidence_dir/config-notes.txt"
import json
import socket
import tomllib
from pathlib import Path
from automation.node_config import NodeConfigError, load_node_config

values = tomllib.loads(Path('configs/node.example.toml').read_text())
values.update(
    origin_url='https://github.com/orientpine/cytoplasm.git',
    require_signed_updates=True,
    deploy_ssh_host='',
    primary_node_name=socket.gethostname(),
    rag_node_name=socket.gethostname(),
    operator_account='root',
)
path = Path('/root/node.toml')
def write_config():
    path.write_text(''.join(f'{key} = {json.dumps(value)}\n' for key, value in values.items()))
write_config()
try:
    load_node_config(path)
except NodeConfigError as error:
    if str(error) != 'deploy_ssh_host must not contain control characters':
        raise
    values['deploy_ssh_host'] = socket.gethostname()
    write_config()
    load_node_config(path)
    print('CONFIG-NOTE: empty deploy_ssh_host rejected by parser; using container hostname')
print('CONFIG-VALID: /root/node.toml (operator_account=root; require_signed_updates=true)')
PY

printf 'WORKING: execute installer command\n'
set +e
docker exec -e PYTHONUNBUFFERED=1 -w /root/autophagy-agents "$container_name" \
    "${command[@]}" 2>&1 | tee "$evidence_dir/install-transcript.txt"
statuses=("${PIPESTATUS[@]}")
set -e
rc=${statuses[0]}
if ((statuses[1] != 0)); then
    echo 'HARNESS-EVIDENCE-FAIL: tee failed' >&2
    exit 125
fi

# Read actual filesystem/account state, never private key contents. This is
# independent evidence that successful result lines reflect real mutations.
verification_rc=0
if "$default_command"; then
    docker exec -i -w /root/autophagy-agents "$container_name" python3 - <<'PY' > "$evidence_dir/mutation-state.txt" 2>&1 || verification_rc=$?
import pwd
import subprocess
from pathlib import Path
from automation.install.assets import build_inputs
from automation.install.state import inspect_state
from automation.node_config import load_node_config

assert Path('/proc/1/comm').read_text().strip() == 'systemd'
config = load_node_config(Path('/root/node.toml'))
inputs = build_inputs(Path.cwd(), config, Path('/root/trust.pub').read_text())
state = inspect_state(inputs)
for account in ('agent', 'peer', 'ops'):
    assert account in state.ready_accounts, f'account not converged: {account}'
    linger = subprocess.check_output(
        ['loginctl', 'show-user', account, '--property=Linger', '--value'], text=True
    ).strip()
    assert linger == 'yes', f'linger not enabled: {account}'
    print(f'ACCOUNT-VERIFIED: {account} uid={pwd.getpwnam(account).pw_uid} Linger={linger}')
assert config.private_root in state.directories
assert config.peer_home / '.ssh' / 'peer_attest_ed25519' in state.peer_attest_keys
print('MUTATION-VERIFIED: directories and peer-attest key ownership/modes/publication')
PY
fi

python3 - "$evidence_dir" "$image_digest" "$revision" "$boot_state" "$rc" "$((SECONDS - started))" "${command[@]}" <<'PY'
import re
import shlex
import sys
from pathlib import Path

folder, digest, revision, boot, rc, elapsed, *command = sys.argv[1:]
root = Path(folder)
lines = (root / 'install-transcript.txt').read_text().splitlines()
plan = [line for line in lines if re.match(r'^\d+\. ', line)]
boundary = next((line for line in lines if '[FAIL]' in line or 'INSTALL-BLOCK' in line), 'none')
# The installer prints the WHOLE plan up front. Only result lines demonstrate
# execution; do not report the final printed action as if it had been reached.
mutation_names = {
    'EnsureAccount': 'account', 'EnsureGroup': 'group',
    'EnsureDirectory': 'directory', 'EnsurePeerAttestKey': 'peer-attest-key',
    'GenerateDeployKey': 'deploy-key', 'InstallGitleaks': 'gitleaks',
    'EnsureRepository': 'repository', 'EnsureFile': 'file', 'EnableTimer': 'timer',
}
reached = -1
for line in lines:
    result = re.match(r'^\[(?:PASS|WARN|FAIL)\] ([^:]+):', line)
    if not result:
        continue
    name = result[1]
    action = mutation_names.get(name, 'check ' + name)
    if name.startswith('trust-key.'):
        action = 'check update-trust'
    # Repeated sub-results from one check refer to the same plan action.
    start = reached if action.startswith('check ') and reached >= 0 else reached + 1
    for index in range(start, len(plan)):
        description = plan[index].split('. ', 1)[1]
        if description == action or description.startswith(action + ' '):
            reached = index
            break
summary = '\n'.join([
    f'image_digest={digest}', f'repository_revision={revision}',
    f'command={shlex.join(command)}', f'rc={rc}', f'boot_state={boot}',
    f'elapsed_seconds={elapsed}', f'first_boundary={boundary}',
    f'highest_action_reached={plan[reached] if reached >= 0 else "none evidenced"}',
]) + '\n'
(root / 'summary.txt').write_text(summary)
print(summary, end='')
PY
printf '\nLast 15 installer lines:\n'
tail -n 15 "$evidence_dir/install-transcript.txt"
if ((verification_rc != 0)); then
    echo 'HARNESS-MUTATION-FAIL: inspect mutation-state.txt' >&2
    tail -n 15 "$evidence_dir/mutation-state.txt" >&2
    exit 125
fi
printf 'Evidence: %s\n' "$evidence_dir"
# Preserve the command's status: a named prerequisite failure is NOT success.
exit "$rc"
