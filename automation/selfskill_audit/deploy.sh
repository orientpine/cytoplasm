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
deploy_provenance_check "$repo_root" "$repo_root/automation/selfskill_audit" || exit 4

run_account() {
  local account="$1" script="$2"
  ssh "$host" "sudo -n -u $account -H bash -lc $(printf '%q' "$script")"
}

for account in "$NODE_AGENT_ACCOUNT" "$NODE_PEER_ACCOUNT"; do
  tar -C "$repo_root/automation/selfskill_audit/cron" -czf - selfskill_audit_watch.py \
    | run_account "$account" 'umask 077; mkdir -p "$HOME/.hermes/scripts"; tar -xzf - -C "$HOME/.hermes/scripts"; chmod 600 "$HOME/.hermes/scripts/selfskill_audit_watch.py"'
  run_account "$account" 'grep -qx "timezone: Asia/Seoul" "$HOME/.hermes/config.yaml"'
  run_account "$account" 'PATH="$HOME/.local/bin:$PATH"; if hermes cron list --all | grep -Eq "Name:[[:space:]]+selfskill-audit-watch$"; then exit 0; fi; hermes cron create "0 9 * * *" --name selfskill-audit-watch --no-agent --script selfskill_audit_watch.py --deliver local'
done
