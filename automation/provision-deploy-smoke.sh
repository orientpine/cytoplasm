#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly RENDER_DIR="$(mktemp -d)"
trap 'rm -rf -- "$RENDER_DIR"' EXIT
readonly UNIT_SRC="$REPO_ROOT/automation/systemd"
readonly UNIT_DIR="${1:-${UNIT_DIR:-/etc/systemd/system}}"
readonly SERVICE="autophagy-deploy-smoke.service"
readonly TIMER="autophagy-deploy-smoke.timer"

log() { printf '[provision-deploy-smoke] %s\n' "$*"; }
die() { log "ERROR: $1" >&2; exit 1; }

is_root() {
  if [[ -n "${DEPLOY_SMOKE_ASSUME_ROOT:-}" ]]; then
    [[ "$DEPLOY_SMOKE_ASSUME_ROOT" == "1" ]]
  else
    [[ "$EUID" == 0 ]]
  fi
}

(( $# <= 1 )) || die "usage: provision-deploy-smoke.sh [unit-dir]"
is_root || die "run as root: sudo bash automation/provision-deploy-smoke.sh"
for command_name in install systemctl; do
  command -v "$command_name" >/dev/null || die "required command missing: $command_name"
done
for source in "$UNIT_SRC/$SERVICE" "$UNIT_SRC/$TIMER"; do
  [[ -f "$source" ]] || die "tracked source missing: $source"
done
python3 "$REPO_ROOT/automation/node_asset_renderer.py" "$UNIT_SRC/$SERVICE" "$RENDER_DIR/$SERVICE"
[[ -f "$REPO_ROOT/automation/deploy-smoke.sh" ]] \
  || die "tracked wrapper missing: automation/deploy-smoke.sh"

install -d -m 0755 -o root -g root "$UNIT_DIR"
install -m 0644 -o root -g root "$RENDER_DIR/$SERVICE" "$UNIT_DIR/"
install -m 0644 -o root -g root "$UNIT_SRC/$TIMER" "$UNIT_DIR/"
systemctl daemon-reload
systemctl enable --now "$TIMER"
log "READY timer=$TIMER"
