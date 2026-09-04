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
deploy_provenance_check "$repo_root" "$repo_root/automation/repair/cron/repair_report_consume_watch.py" || exit 4

run_account() {
  local account="$1" script="$2"
  ssh "$host" "sudo -n -u $account -H bash -lc $(printf '%q' "$script")"
}

# 소비 감시는 cron sandbox가 찾는 계정 홈에만 복사해야 수리 런타임 권한을 넓히지 않는다.
tar -C "$repo_root/automation/repair/cron" -czf - repair_report_consume_watch.py \
  | run_account "$NODE_AGENT_ACCOUNT" 'umask 077; mkdir -p "$HOME/.hermes/scripts"; tar -xzf - -C "$HOME/.hermes/scripts"; chmod 600 "$HOME/.hermes/scripts/repair_report_consume_watch.py"; sha256sum "$HOME/.hermes/scripts/repair_report_consume_watch.py"'
