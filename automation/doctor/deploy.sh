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
deploy_provenance_check "$repo_root" "$repo_root/automation/doctor" || exit 4

run_account() {
  local account="$1" script="$2"
  ssh "$host" "sudo -n -u $account -H bash -lc $(printf '%q' "$script")"
}

source "$repo_root/automation/deploy_push.sh"
source "$repo_root/automation/deploy_cron.sh"
for account in "$NODE_AGENT_ACCOUNT" "$NODE_PEER_ACCOUNT"; do
  run_agent() { run_account "$account" "$1"; }
  push_file "$repo_root/automation/doctor/cron/doctor_watch.py" '.hermes/scripts/doctor_watch.py'
  converge_cron doctor-watch "17 * * * *" doctor_watch.py local
done
