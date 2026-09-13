#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}"
if [ -z "$host" ]; then
  echo "DEPLOY-BLOCK: DEPLOY_SSH_HOST is unset. Export it (or configure ~/.hermes/node.toml)" >&2
  echo "              and re-run; refusing to ssh to an unresolvable placeholder." >&2
  exit 3
fi

source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/reminder_poller/poll_reminders.py" || exit 4

run_account() {
  local account="$1" script="$2"
  ssh "$host" "sudo -n -u $account -H bash -lc $(printf '%q' "$script")"
}

# 알림 폴러는 cron sandbox가 찾는 계정 홈에만 복사해야 다른 계정의 상태를 읽지 않는다.
# 새 래퍼를 쓰기 전에 플랫 import 동반 파일을 먼저 갱신한다.
tar -C "$repo_root/automation/reminder_poller" -czf - poller_core.py reminder_store.py \
  | run_account "$NODE_AGENT_ACCOUNT" 'umask 077; mkdir -p "$HOME/.hermes/reminder_poller_runtime"; tar -xzf - -C "$HOME/.hermes/reminder_poller_runtime"; chmod 600 "$HOME/.hermes/reminder_poller_runtime/"{poller_core,reminder_store}.py; sha256sum "$HOME/.hermes/reminder_poller_runtime/"{poller_core,reminder_store}.py'
tar -C "$repo_root/automation/reminder_poller" -czf - poll_reminders.py \
  | run_account "$NODE_AGENT_ACCOUNT" 'umask 077; mkdir -p "$HOME/.hermes/scripts"; tar -xzf - -C "$HOME/.hermes/scripts"; chmod 600 "$HOME/.hermes/scripts/poll_reminders.py"; sha256sum "$HOME/.hermes/scripts/poll_reminders.py"'
