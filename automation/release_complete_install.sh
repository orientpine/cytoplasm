#!/usr/bin/env bash
# Install the workstation user's release-completion timer.
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
readonly UNIT_SOURCE="$REPO_ROOT/automation/systemd/user"
readonly UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
readonly SERVICE="autophagy-release-complete.service"
readonly TIMER="autophagy-release-complete.timer"

usage() {
  printf 'usage: release_complete_install.sh [--print|--status|--uninstall]\n'
}

(( EUID != 0 )) || {
  printf '[release-complete-install] ERROR: run as the workstation user, not root\n' >&2
  exit 4
}

case "${1:-}" in
  "") mode=install ;;
  --print) mode=print ;;
  --status) mode=status ;;
  --uninstall) mode=uninstall ;;
  --help|-h) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
(( $# <= 1 )) || { usage >&2; exit 2; }

render() {
  local source="$1" escaped
  escaped="${REPO_ROOT//\\/\\\\}"
  escaped="${escaped//&/\\&}"
  escaped="${escaped//|/\\|}"
  sed 's|$RELEASE_COMPLETE_REPO|'"$escaped"'|g' "$source"
}

for unit in "$SERVICE" "$TIMER"; do
  [[ -f "$UNIT_SOURCE/$unit" ]] || {
    printf '[release-complete-install] ERROR: missing unit source: %s\n' "$UNIT_SOURCE/$unit" >&2
    exit 1
  }
done

case "$mode" in
  print)
    for unit in "$SERVICE" "$TIMER"; do
      printf '# ==> %s\n' "$unit"
      render "$UNIT_SOURCE/$unit"
    done
    ;;
  status)
    systemctl --user list-timers "$TIMER" --no-pager || true
    systemctl --user status "$SERVICE" --no-pager -n 20 || true
    ;;
  uninstall)
    systemctl --user disable --now "$TIMER" >/dev/null 2>&1 || true
    rm -f -- "$UNIT_DIR/$SERVICE" "$UNIT_DIR/$TIMER"
    systemctl --user daemon-reload
    ;;
  install)
    install -d -m 0755 "$UNIT_DIR"
    render "$UNIT_SOURCE/$SERVICE" | install -m 0644 /dev/stdin "$UNIT_DIR/$SERVICE"
    render "$UNIT_SOURCE/$TIMER" | install -m 0644 /dev/stdin "$UNIT_DIR/$TIMER"
    systemctl --user daemon-reload
    systemctl --user enable --now "$TIMER"
    systemctl --user list-timers "$TIMER" --no-pager
    ;;
esac
