#!/usr/bin/env bash
set -euo pipefail

usage() { echo '사용법: run.sh [--evidence-dir DIR] [--operator NAME] [--keep] [--stub-hermes] [-- <명령...>]'; }

harness_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo=$(git -C "$harness_dir" rev-parse --show-toplevel)
evidence_dir=/tmp/install-systemd-qa keep=false stub_hermes=false operator=root
while (($#)); do
    case "$1" in
        --evidence-dir)
            if (($# < 2)) || [[ -z $2 ]]; then usage >&2; exit 2; fi
            evidence_dir=$2; shift 2 ;;
        # 이름은 useradd·노드 설정·sudoers 자산에 리터럴로 들어간다. 그대로 실어도
        # 안전한 POSIX 계정 이름만 받는다 — docker 를 건드리기 전에 막는다.
        --operator)
            if (($# < 2)) || [[ ! $2 =~ ^[a-z_][a-z0-9_-]{0,30}$ ]]; then usage >&2; exit 2; fi
            operator=$2; shift 2 ;;
        --keep) keep=true; shift ;;
        --stub-hermes) stub_hermes=true; shift ;;
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
temporary='' boot_directory='' container_attempted=false
cleanup() {
    local rc=$?
    trap - EXIT
    if [[ -n $temporary ]] && ! rm -rf -- "$temporary"; then
        echo 'HARNESS-CLEANUP-FAIL: 임시 키 디렉터리 삭제 실패' >&2; rc=125
    fi
    if "$container_attempted"; then
        if "$keep"; then
            printf 'Kept container: %s (remove with docker rm -f %s)\n' "$container_name" "$container_name"
        elif ! docker rm -f "$container_name" > /dev/null; then
            echo "HARNESS-CLEANUP-FAIL: 컨테이너 삭제 실패: $container_name" >&2; rc=125
        fi
    fi
    if [[ -n $boot_directory ]]; then
        if "$keep"; then
            printf 'After removing the kept container: rm -rf -- %q\n' "$boot_directory"
        elif ! rm -rf -- "$boot_directory"; then
            echo 'HARNESS-CLEANUP-FAIL: 부팅 알림 디렉터리 삭제 실패' >&2; rc=125
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
from __future__ import annotations
import socket, subprocess, sys, time
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
        subprocess.run(['docker', 'run', '-d', '--name', name, '--hostname', name,
            '--privileged', '--cgroupns=host', '--tmpfs', '/run', '--tmpfs', '/run/lock',
            '-v', '/sys/fs/cgroup:/sys/fs/cgroup:rw',
            '--mount', f'type=bind,src={temporary},dst=/run/host', image,
            '/sbin/init', 'systemd.set_credential=vmm.notify_socket:/run/host/notify',
        ], check=True, timeout=remaining())
        while True:
            receiver.settimeout(remaining())
            if b'READY=1' not in receiver.recv(4096).splitlines():
                continue
            result = subprocess.run(['docker', 'exec', name, 'systemctl', 'is-system-running', '--wait'],
                                    capture_output=True, text=True, timeout=remaining())
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
git -C "$repo" archive "$revision" | docker exec -i "$container_name" tar -x -C /root/autophagy-agents --exclude='.omo' --exclude='.venv' --exclude='.env.secrets'

temporary=$(mktemp -d)
ssh-keygen -q -t ed25519 -N '' -C install-systemd-qa -f "$temporary/trust"
docker cp "$temporary/trust.pub" "$container_name:/root/trust.pub"
rm -rf -- "$temporary"
temporary=''

# 운영자는 서비스 계정이 아니라 사람이라 설치기가 만들지 않는다. 결함이 나타나는 조건은
# 운영자가 배포 체크아웃(ops:autophagy 2750)을 읽을 수 없는 평범한 계정인 것이므로,
# 서비스 그룹에 넣지 않은 채로 만든다.
if [[ $operator != root ]]; then
    printf 'WORKING: 비-root 운영자 계정 생성 %s\n' "$operator"
    docker exec "$container_name" useradd --create-home --shell /bin/bash -- "$operator"
fi

docker exec -i -w /root/autophagy-agents "$container_name" python3 - "$operator" <<'PY' | tee "$evidence_dir/config-notes.txt"
from __future__ import annotations
import json, socket, sys, tomllib
from pathlib import Path
from automation.node_config import NodeConfigError, load_node_config

operator = sys.argv[1]
values = tomllib.loads(Path('configs/node.example.toml').read_text())
values.update(origin_url='https://github.com/orientpine/cytoplasm.git',
              require_signed_updates=True, deploy_ssh_host='', operator_account=operator,
              primary_node_name=socket.gethostname(), rag_node_name=socket.gethostname())
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
print(f'CONFIG-VALID: /root/node.toml (operator_account={operator}; require_signed_updates=true)')
PY

: > "$evidence_dir/install-transcript.txt"
run_installer() {
    printf 'WORKING: 설치 명령 실행\n'
    set +e
    docker exec -e PYTHONUNBUFFERED=1 -w /root/autophagy-agents "$container_name" "${command[@]}" 2>&1 | tee -a "$evidence_dir/install-transcript.txt"
    statuses=("${PIPESTATUS[@]}")
    set -e
    rc=${statuses[0]}
    if ((statuses[1] != 0)); then echo 'HARNESS-EVIDENCE-FAIL: 증적 기록 실패' >&2; exit 125; fi
}
run_installer
if "$stub_hermes"; then
    if ! grep -q '^\[FAIL\] hermes-gateway:' "$evidence_dir/install-transcript.txt"; then
        echo 'HARNESS-STUB-FAIL: 1차 실행이 hermes-gateway 경계에 도달하지 않았다' >&2; exit 125
    fi
    printf 'WORKING: Hermes 시험용 스텁 설치\n'
    docker exec -i -w /root/autophagy-agents "$container_name" python3 - <<'PY'
from __future__ import annotations
import os, pwd, subprocess
from pathlib import Path
from automation.node_config import load_node_config

config = load_node_config(Path('/root/node.toml'))
for account, home, unit in ((config.agent_account, config.agent_home, config.agent_gateway_unit),
                            (config.peer_account, config.peer_home, config.peer_gateway_unit)):
    user = pwd.getpwnam(account)
    files = (
        (home / '.local/bin/hermes', 0o755, '#!/bin/sh\n[ "$1" = --version ] || exit 2\necho "hermes 0.0.0-harness-stub"\n'),
        (home / '.config/systemd/user' / unit, 0o644, '[Service]\nType=simple\nExecStart=/bin/sleep infinity\n\n[Install]\nWantedBy=default.target\n'),
    )
    for path, mode, content in files:
        subprocess.run(['install', '-d', '-o', account, '-g', str(user.pw_gid), str(path.parent)], check=True)
        path.write_text(content)
        path.chmod(mode)
        os.chown(path, user.pw_uid, user.pw_gid)
    subprocess.run(['systemctl', 'start', f'user@{user.pw_uid}.service'], check=True)
    prefix = ['runuser', '-u', account, '--', 'env', f'HOME={home}', f'XDG_RUNTIME_DIR=/run/user/{user.pw_uid}', 'systemctl', '--user']
    subprocess.run([*prefix, 'daemon-reload'], check=True)
    subprocess.run([*prefix, 'enable', '--now', unit], check=True)
PY
    printf '\n===== HARNESS-PASS-2: Hermes 스텁 적용 후 재실행 =====\n' | tee -a "$evidence_dir/install-transcript.txt"
    run_installer
fi

# Read actual filesystem/account state, never private key contents. This is
# independent evidence that successful result lines reflect real mutations.
verification_rc=0
if "$default_command"; then
    docker exec -i -w /root/autophagy-agents "$container_name" python3 - \
        < "$harness_dir/verify_mutations.py" > "$evidence_dir/mutation-state.txt" 2>&1 || verification_rc=$?
fi

python3 - "$evidence_dir" "$image_digest" "$revision" "$boot_state" "$rc" "$((SECONDS - started))" "$stub_hermes" "$operator" "${command[@]}" <<'PY'
from __future__ import annotations
import re, shlex, sys
from pathlib import Path

folder, digest, revision, boot, rc, elapsed, stub, operator, *command = sys.argv[1:]
root = Path(folder)
lines = (root / 'install-transcript.txt').read_text().split('===== HARNESS-PASS-2: Hermes 스텁 적용 후 재실행 =====\n')[-1].splitlines()
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
    f'elapsed_seconds={elapsed}', f'stub_hermes={stub}', f'operator_account={operator}',
    f'first_boundary={boundary}',
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
