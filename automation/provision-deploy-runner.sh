#!/usr/bin/env bash
# automation/provision-deploy-runner.sh — create the unprivileged account a CI runner
# executes as, and grant it the one command it may escalate.
#
# WHY the account is separate (MD-3): a self-hosted runner executes whatever the
# workflow file says, and that file changes with every merge. So the question is never
# "is the workflow safe" but "what can the account it runs as reach". Running it as
# `ops` was rejected for exactly that: `ops` can read /srv/autophagy-private — the
# repair push key lives there — and holds the release-install grant, so merging a PR
# would have become an escalation path.
#
# `deploy-runner` therefore exists to be boring. No supplementary groups at all, most
# sharply not `docker`: /var/run/docker.sock is present on this node (root:docker), and
# membership in that group is root by another name. Its sudoers grant names one
# absolute path with NO arguments and NO wildcard, so the privileged surface is a
# single fixed command rather than a command family.
#
# WHY it is its OWN file: the active repair-report-rollout plan claims the account
# bootstrap script as its sole source change, so touching it here would overwrite
# another session's settled work. provision-release-store.sh sidestepped the same
# collision the same way.
#
# The runner tree is root-owned and read-only to the runner except its work and diag
# directories. Writable binaries or hook configuration would let one workflow persist
# itself into every later job.
#
# Idempotent: the account is created only when absent, and `install` replaces single
# paths. Re-run it after changing the sudoers source.
#
# Env (test seams; default to the real node paths):
#   RUNNER_ROOT               default /srv/actions-runner
#   SUDOERS_PATH              default /etc/sudoers.d/autophagy-deploy-runner
#   DEPLOY_RUNNER_ASSUME_ROOT override the root check for hermetic tests
set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
eval "$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env)"
readonly RUNNER_USER="deploy-runner"
readonly RUNNER_ROOT="${RUNNER_ROOT:-$NODE_SERVICE_ROOT/actions-runner}"
readonly SUDOERS_PATH="${SUDOERS_PATH:-/etc/sudoers.d/autophagy-deploy-runner}"

log() { printf '[provision-deploy-runner] %s\n' "$*"; }
die() { log "ERROR: $1" >&2; exit 1; }

is_root() {
  if [[ -n "${DEPLOY_RUNNER_ASSUME_ROOT:-}" ]]; then
    [[ "$DEPLOY_RUNNER_ASSUME_ROOT" == "1" ]]
  else
    [[ "$EUID" == 0 ]]
  fi
}
is_root || die "run as root: sudo bash automation/provision-deploy-runner.sh"

for command_name in install useradd visudo; do
  command -v "$command_name" >/dev/null || die "required command missing: $command_name"
done

readonly SUDOERS_SRC="$REPO_ROOT/automation/sudoers.d/autophagy-deploy-runner"
[[ -f "$SUDOERS_SRC" ]] || die "tracked sudoers source missing: $SUDOERS_SRC"

# The account: system, no login shell, and deliberately no -G. Every group this is not
# in is a decision, not an omission.
if ! id -u "$RUNNER_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/home/$RUNNER_USER" \
    --shell /usr/sbin/nologin "$RUNNER_USER"
  log "created account $RUNNER_USER"
fi

# Runner tree: root-owned. Only the disposable areas are handed over.
install -d -m 0755 -o root -g root "$RUNNER_ROOT"
install -d -m 0750 -o root -g root "$RUNNER_ROOT/_work" "$RUNNER_ROOT/_diag"
chown "$RUNNER_USER:$RUNNER_USER" "$RUNNER_ROOT/_work" "$RUNNER_ROOT/_diag"

install -d -m 0755 -o root -g root "$(dirname "$SUDOERS_PATH")"
python3 "$REPO_ROOT/automation/node_asset_renderer.py" "$SUDOERS_SRC" "$SUDOERS_PATH.tmp"
install -m 0440 -o root -g root "$SUDOERS_PATH.tmp" "$SUDOERS_PATH"
rm -f "$SUDOERS_PATH.tmp"
visudo -cf "$SUDOERS_PATH" >/dev/null

log "READY user=$RUNNER_USER runner_root=$RUNNER_ROOT sudoers=$SUDOERS_PATH"
