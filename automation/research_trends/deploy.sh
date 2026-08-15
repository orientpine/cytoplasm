#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-$NODE_DEPLOY_SSH_HOST}"

run_agent() {
  local script="$1"
  ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "$script")"
}

push_file() {
  local source="$1" destination="$2"
  run_agent "umask 077; mkdir -p \"\$HOME/$(dirname "$destination")\"; cat > \"\$HOME/$destination\"; chmod 600 \"\$HOME/$destination\"" < "$source"
}

# Deploy guard: refuse to push code that origin/main does not have (see the header of
# automation/deploy_provenance.sh for why a silent revert is otherwise inevitable).
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/automation/research_trends/research_trends.py" \
  "$repo_root/automation/research_trends/research_trends_core.py" \
  "$repo_root/configs/sensitivity-rules.yaml" \
  "$repo_root/prompts/research-trends-v1.md" || exit 4

deploy_archive_stream "$repo_root" "$repo_root/automation/research_trends" research_trends.py research_trends_core.py \
  | run_agent 'umask 077; rm -rf "$HOME/.hermes/research_trends_runtime"; mkdir -p "$HOME/.hermes/research_trends_runtime"; tar -xzf - -C "$HOME/.hermes/research_trends_runtime"; chmod 600 "$HOME/.hermes/research_trends_runtime"/*.py'
push_file "$repo_root/automation/research_trends/research_trends.py" '.hermes/scripts/research_trends.py'
push_file "$repo_root/configs/sensitivity-rules.yaml" '.hermes/sensitivity-rules.yaml'
push_file "$repo_root/prompts/research-trends-v1.md" '.hermes/research-trends/research-trends-v1.md'

run_agent 'grep -qx "timezone: Asia/Seoul" "$HOME/.hermes/config.yaml"'
run_agent 'PATH="$HOME/.local/bin:$PATH"; if hermes cron list | grep -Eq "Name:[[:space:]]+research-trends$"; then exit 0; fi; hermes cron create "0 9 * * 1" --name research-trends --no-agent --script research_trends.py --deliver local'
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list'
