#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-$NODE_DEPLOY_SSH_HOST}"

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

deploy_archive_stream "$repo_root" "$repo_root/automation/regression_bank" bank_state.py weekly_bank.py \
  | run_agent 'umask 077; rm -rf "$HOME/.hermes/regression_bank_runtime"; mkdir -p "$HOME/.hermes/regression_bank_runtime"; tar -xzf - -C "$HOME/.hermes/regression_bank_runtime"; chmod 600 "$HOME/.hermes/regression_bank_runtime"/*.py'

run_agent 'chmod 2711 /srv/autophagy-agents/logs; test -d /srv/autophagy-agents/logs'
run_agent 'grep -qx "timezone: Asia/Seoul" "$HOME/.hermes/config.yaml"'
# The agent-local weekly cron is structurally broken; deploy the RAG-node runner separately.
