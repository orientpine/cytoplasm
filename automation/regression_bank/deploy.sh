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

run_agent() {
  local script="$1"
  ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "$script")"
}

# Deploy guard: refuse to push code that origin/main does not have (see the header of
# automation/deploy_provenance.sh for why a silent revert is otherwise inevitable).
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/automation/regression_bank/bank_state.py" \
  "$repo_root/automation/regression_bank/weekly_bank.py" || exit 4

# The runtime is a row of the standing runtime-package table: keep it a real directory
# (no --link) and ship exactly the declared files, swapped in after staging verification.
# shellcheck source=automation/deploy_tree.sh
source "$repo_root/automation/deploy_tree.sh"
deploy_tree_swap "$repo_root/automation/regression_bank" .hermes/regression_bank_runtime bank_state.py weekly_bank.py

run_agent 'chmod 2711 /srv/autophagy-agents/logs; test -d /srv/autophagy-agents/logs'
run_agent 'grep -qx "timezone: Asia/Seoul" "$HOME/.hermes/config.yaml"'
# The agent-local weekly cron is structurally broken; deploy the RAG-node runner separately.
