#!/usr/bin/env bash
# automation/provision-deploy-reconcile.sh — install and start the node-side reconciler
# that keeps the release runtime at origin/main (MD-2).
#
# WHY it is one script rather than four root commands in a handoff message: every step
# here is `sudo` on prod, and a typo in a hand-typed root command is its own outage. It
# is also idempotent, so re-running after a change is the normal way to use it.
#
# WHY it refuses to enable a timer it cannot prove works: the failure mode of this
# feature is SILENCE. The reconciler's notifier is still `unconfigured_notifier`, so a
# timer that wakes every two minutes and is denied sudo would fail forever without
# telling anyone — a node that looks healthy and never converges. That exact gap shipped
# once: the service runs as `ops` while the only helper grant named `deploy-runner`
# (2026-08-01). So the grant is VERIFIED with `sudo -l` before the timer is started, and
# a missing helper is a hard stop rather than a warning.
#
# The account is read from the unit's own `User=` — one source of truth, so changing the
# unit cannot silently leave the grant pointing at the wrong account.
#
# Env (test seams; default to the real node paths):
#   SUDOERS_PATH                  default /etc/sudoers.d/autophagy-deploy-reconcile
#   UNIT_DIR                      default /etc/systemd/system
#   STATE_DIR                     default /srv/autophagy-private/deploy-reconcile
#   HELPER_PATH                   default /usr/local/libexec/autophagy-converge-origin-main
#   DEPLOY_RECONCILE_ASSUME_ROOT  override the root check for hermetic tests
#   DEPLOY_RECONCILE_NO_ENABLE=1  install everything but leave the timer stopped
set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
eval "$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env)"
readonly SUDOERS_PATH="${SUDOERS_PATH:-/etc/sudoers.d/autophagy-deploy-reconcile}"
readonly UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
readonly STATE_DIR="${STATE_DIR:-$NODE_PRIVATE_ROOT/deploy-reconcile}"
readonly HELPER_PATH="${HELPER_PATH:-$NODE_LIBEXEC_DIR/autophagy-converge-origin-main}"
readonly TIMER="autophagy-deploy-reconcile.timer"

log() { printf '[provision-deploy-reconcile] %s\n' "$*"; }
die() { log "ERROR: $1" >&2; exit 1; }

is_root() {
  if [[ -n "${DEPLOY_RECONCILE_ASSUME_ROOT:-}" ]]; then
    [[ "$DEPLOY_RECONCILE_ASSUME_ROOT" == "1" ]]
  else
    [[ "$EUID" == 0 ]]
  fi
}
is_root || die "run as root: sudo bash automation/provision-deploy-reconcile.sh"

for command_name in install visudo sudo systemctl; do
  command -v "$command_name" >/dev/null || die "required command missing: $command_name"
done

readonly SUDOERS_SRC="$REPO_ROOT/automation/sudoers.d/autophagy-deploy-reconcile"
readonly UNIT_SRC="$REPO_ROOT/automation/systemd"
readonly RENDER_DIR="$(mktemp -d)"
trap 'rm -rf -- "$RENDER_DIR"' EXIT
python3 "$REPO_ROOT/automation/node_asset_renderer.py" "$SUDOERS_SRC" "$RENDER_DIR/sudoers"
python3 "$REPO_ROOT/automation/node_asset_renderer.py" "$UNIT_SRC/autophagy-deploy-reconcile.service" "$RENDER_DIR/service"
for src in "$SUDOERS_SRC" "$UNIT_SRC/autophagy-deploy-reconcile.service" "$UNIT_SRC/$TIMER"; do
  [[ -f "$src" ]] || die "tracked source missing: $src"
done

# The helper is what the tick escalates to. Without it the timer is a no-op that fails
# every two minutes, and right now nothing would say so.
[[ -x "$HELPER_PATH" ]] \
  || die "converge helper missing: $HELPER_PATH — run provision-deploy-converge.sh first"

SERVICE_USER="$(sed -n 's/^User=\(.*\)$/\1/p' "$RENDER_DIR/service")"
[[ -n "$SERVICE_USER" ]] || die "the reconcile service does not name the account it runs as"
id -u "$SERVICE_USER" >/dev/null 2>&1 || die "service account does not exist: $SERVICE_USER"

install -d -m 0755 -o root -g root "$(dirname "$SUDOERS_PATH")"
install -m 0440 -o root -g root "$RENDER_DIR/sudoers" "$SUDOERS_PATH"
visudo -cf "$SUDOERS_PATH" >/dev/null

# Syntactically valid is not the same as effective. Ask sudo what the account may
# actually run — this is the check whose absence let the gap ship.
sudo -n -l -U "$SERVICE_USER" 2>/dev/null | grep -Fq "$HELPER_PATH" \
  || die "$SERVICE_USER still may not run $HELPER_PATH — refusing to start a timer that cannot converge"

install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATE_DIR"
install -d -m 0755 -o root -g root "$UNIT_DIR"
install -m 0644 -o root -g root "$RENDER_DIR/service" "$UNIT_DIR/autophagy-deploy-reconcile.service"
install -m 0644 -o root -g root "$UNIT_SRC/$TIMER" "$UNIT_DIR/"
systemctl daemon-reload

if [[ "${DEPLOY_RECONCILE_NO_ENABLE:-}" == "1" ]]; then
  log "READY (timer left stopped) user=$SERVICE_USER state=$STATE_DIR"
  exit 0
fi
systemctl enable --now "$TIMER"
log "READY user=$SERVICE_USER state=$STATE_DIR timer=$TIMER"
