#!/usr/bin/env bash
# automation/converge_origin_main.sh — the ONLY privileged entrypoint an automated
# trigger may call. Source of /usr/local/libexec/autophagy-converge-origin-main.
#
# WHY it exists (MD-1, 2026-08-01): to make "merge a PR" mean "prod runs it", something
# must converge the runtime without a human. The obvious shape — let a CI runner sudo
# the existing converger — is an escalation: the runner would get a shell as an account
# that can reach /srv/autophagy-private and the deployment state, AND the converger it
# runs is itself replaced by every merge. Merging a PR would then be arbitrary code
# execution under sudo. This helper removes both halves of that.
#
# CONTRACT (each clause is pinned by tests/unit/test_provision_deploy_converge.py):
#   * NO ARGUMENTS, AND NO NODE CONFIGURATION. It resolves the trusted release from
#     signatures alone, so neither a caller nor a file an unprivileged account can
#     write may aim it at a sha. It used to pass `~ops/.hermes/node.toml` to the
#     verifier; `require_signed_updates = false` in that file made the verifier hand
#     back the raw unsigned `origin/main`, and ops holds NOPASSWD sudo for this helper
#     — one file in its own home reinstated the very escalation below (2026-08-21).
#   * NO INHERITED ENVIRONMENT. PATH is fixed; PYTHONPATH/GIT_*/SSH_*/LD_* and any
#     RELEASE_EXPECTED_SHA are cleared before anything runs.
#   * NEVER EXECUTES THE MUTABLE RUNTIME TREE. Its dependencies are the root-owned
#     copies installed beside it; the release tree changes with every merge and must
#     never be executed under privilege.
#   * SAME LOCK as converge-release-runtime.sh. Two convergence paths holding different
#     locks can install out of order and flip the runtime BACKWARDS onto an older sha.
#   * PRIVILEGE SPLIT. Git work runs as ops (writing to the ops-owned mirror as root
#     would leave root-owned objects that later break every ff-pull); only the install
#     step runs as root.
#
# Exit codes: 0 converged (or already at the trusted target) · 4 preconditions unmet
#             5 another convergence holds the lock (transient — the caller retries)
#             1 convergence failed
set -euo pipefail

# --- Env hygiene: a privileged helper inherits nothing from its caller. -------------
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
unset GIT_SSH GIT_SSH_COMMAND GIT_CONFIG GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM
unset SSH_AUTH_SOCK SSH_AGENT_PID
unset LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT
unset RELEASE_EXPECTED_SHA
umask 022

readonly MIRROR=$NODE_DEPLOY_CHECKOUT
readonly STORE_PARENT=$NODE_SERVICE_ROOT
readonly LIBDIR=$NODE_LIBEXEC_DIR/autophagy-converge.d
readonly SNAPSHOT="$LIBDIR/origin_snapshot.sh"
readonly RELEASE_STORE="$LIBDIR/release_store.py"
readonly UPDATE_TRUST="$LIBDIR/automation/update_trust.py"
readonly ALLOWED_SIGNERS="/etc/autophagy/update-allowed-signers"
# Root owns the authoritative rollback-prevention anchor. ops can read it for the
# pre-gate but cannot erase or advance it.
readonly RELEASE_FLOOR=/var/lib/autophagy/update-trust/release-floor.json
readonly UPDATE_CHANNEL_STATE=$NODE_PRIVATE_ROOT/deploy-reconcile/update-channel.json
# The same file every other caller opens. See converge-release-runtime.sh.
#
# NOT in /tmp. This lock is shared across accounts (this side is root, the deploy side
# is ops) and `fs.protected_regular=2` refuses an open in a sticky, world-writable
# directory when the file's owner differs from the opener — root included. Measured
# 2026-08-01: the timer converged nothing for hours because ops had created
# /tmp/autophagy-release-converge.lock first and root could never open it again.
readonly LOCK_DIR=$NODE_PRIVATE_ROOT/locks
readonly LOCK="$LOCK_DIR/release-converge.lock"
readonly LOCK_GROUP=$NODE_SERVICE_GROUP
readonly OPS_USER=$NODE_OPS_ACCOUNT

log() { printf '[converge-origin-main] %s\n' "$*" >&2; }
die() { log "$1"; exit "${2:-1}"; }

[[ "$EUID" == 0 ]] || die "SYNC-BLOCK: must run as root (via sudo)" 4
[[ -x "$SNAPSHOT" ]] || die "SYNC-BLOCK: missing root-owned snapshot primitive $SNAPSHOT" 4
[[ -f "$RELEASE_STORE" ]] || die "SYNC-BLOCK: missing root-owned release store $RELEASE_STORE" 4
[[ -f "$UPDATE_TRUST" ]] || die "SYNC-BLOCK: missing root-owned update verifier $UPDATE_TRUST" 4
[[ -d "$MIRROR/.git" ]] || die "SYNC-BLOCK: $MIRROR is not a checkout" 4

as_ops() { runuser -u "$OPS_USER" -- "${@}"; }

# --- Resolve and verify the target ourselves. No caller gets a say. ----------------
remote_env=()
if [[ -e "$UPDATE_CHANNEL_STATE" ]]; then
  update_channel="$(python3 -I - "$UPDATE_CHANNEL_STATE" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    payload = json.load(stream)
if (
    not isinstance(payload, dict)
    or set(payload) != {"update_channel", "version"}
    or payload["version"] != 1
):
    raise SystemExit("invalid update-channel binding")
channel = payload["update_channel"]
if channel is None:
    raise SystemExit(0)
if not isinstance(channel, str) or not channel.strip():
    raise SystemExit("invalid update channel")
sys.stdout.write(channel)
PY
  )" || die "SYNC-BLOCK: invalid update-channel binding" 4
  if [[ -n "$update_channel" ]]; then
    remote_env=(
      env
      GIT_CONFIG_COUNT=1
      GIT_CONFIG_KEY_0=remote.origin.url
      "GIT_CONFIG_VALUE_0=$update_channel"
    )
  fi
fi
pre_gate_target="$(as_ops "${remote_env[@]}" python3 -I "$UPDATE_TRUST" resolve-signed --mirror "$MIRROR" --allowed-signers "$ALLOWED_SIGNERS" --floor-path "$RELEASE_FLOOR")" \
  || die "SYNC-BLOCK: update trust rejected the remote target" 4
[[ "$pre_gate_target" =~ ^[0-9a-f]{40,64}$ ]] \
  || die "SYNC-BLOCK: verifier returned an invalid target" 4

# Re-verify with root-owned code executed as ops, because Git must stay ops-owned and
# Git refuses a cross-owner checkout. Root performs only the final monotonic write,
# whose comparison cannot lower an existing floor.
verified="$(as_ops "${remote_env[@]}" python3 -I - "$LIBDIR" "$MIRROR" "$ALLOWED_SIGNERS" "$RELEASE_FLOOR" "${update_channel:-}" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from automation.update_trust import resolve_signed_update

mirror, signers, floor = map(Path, sys.argv[2:5])
release = resolve_signed_update(
    mirror,
    signers,
    remote_url=sys.argv[5] or None,
    floor_path=floor,
)
print(release.tag, release.commit_sha)
PY
)" || die "SYNC-BLOCK: privileged update trust rejected the remote target" 4
read -r verified_tag target extra <<<"$verified"
[[ -z "${extra:-}" ]] && [[ "$target" =~ ^[0-9a-f]{40,64}$ ]] \
  || die "SYNC-BLOCK: privileged verifier returned an invalid release" 4
[[ "$target" == "$pre_gate_target" ]] \
  || die "SYNC-BLOCK: signed target changed between verification passes" 4
python3 -I - "$LIBDIR" "$RELEASE_FLOOR" "$verified_tag" "$target" <<'PY' \
  || die "SYNC-BLOCK: cannot advance authoritative release floor" 4
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from automation.update_trust_state import privileged_advance_release_floor

privileged_advance_release_floor(Path(sys.argv[2]), sys.argv[3], sys.argv[4])
PY

# --- Already there? Say nothing and cost nothing. -----------------------------------
if python3 -I "$RELEASE_STORE" current --verify "$target" --store-root "$STORE_PARENT" \
     >/dev/null 2>&1; then
  log "already at $target"
  exit 0
fi

# --- Serialize with every other convergence path. ----------------------------------
# setgid on the directory so the file's group is the same whoever creates it, and 007
# so the other account can open it. Root bypasses DAC; ops is the one that needs this.
install -d -m 2770 -o "$OPS_USER" -g "$LOCK_GROUP" "$LOCK_DIR" \
  || die "SYNC-BLOCK: cannot prepare lock directory $LOCK_DIR" 4
[[ -e "$LOCK" ]] || (umask 007; : > "$LOCK") \
  || die "SYNC-BLOCK: cannot create shared lock $LOCK" 4
exec 9>"$LOCK"
flock -x -w 600 9 || die "SYNC-BLOCK: another release convergence is still running" 5

log "converging to $target"
# Snapshot + tar as ops (mirror stays ops-owned); install as root. The snapshot
# primitive re-pins the verified commit and fails closed if origin moved under us.
as_ops "${remote_env[@]}" env AUTOPHAGY_SNAPSHOT_TARGET="$target" bash -c '
  set -euo pipefail
  . '"$SNAPSHOT"'
  # No -C: origin_snapshot_run already runs the command with cwd = the snapshot tree.
  # Passing -C "$AUTOPHAGY_SNAPSHOT_DIR" here would be expanded by THIS shell while it
  # builds the argument list — before origin_snapshot_run sets it — so under `set -u`
  # it aborted with "unbound variable" and piped an empty archive into the store
  # (measured 2026-08-02: RELEASE-STORE-BLOCK: archive extraction failed: empty file).
  # The ops-side converger escapes this by wrapping tar in its own `bash -c`; relying
  # on the documented cwd contract is the same fix with less quoting.
  origin_snapshot_run '"$MIRROR"' "$AUTOPHAGY_SNAPSHOT_TARGET" \
    tar --exclude=.git -czf - .
' | python3 -I "$RELEASE_STORE" install --sha "$target" --store-root "$STORE_PARENT" --git-root "$MIRROR" \
  || die "convergence failed for $target"

python3 -I "$RELEASE_STORE" current --verify "$target" --store-root "$STORE_PARENT" >/dev/null \
  || die "the runtime did not end at $target"

log "converged: current -> $target"
