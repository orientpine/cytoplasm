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
  "$repo_root/automation/notes_organize/notes_organize.py" || exit 4

push_file "$repo_root/automation/notes_organize/notes_organize.py" '.hermes/scripts/notes_organize.py'
run_agent 'grep -qx "timezone: Asia/Seoul" "$HOME/.hermes/config.yaml"'
run_agent 'PATH="$HOME/.local/bin:$PATH"; if hermes cron list | grep -Eq "Name:[[:space:]]+notes-weekly-organize$"; then exit 0; fi; hermes cron create "0 8 * * 1" --name notes-weekly-organize --no-agent --script notes_organize.py --deliver local'
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list'
