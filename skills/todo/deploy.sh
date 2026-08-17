#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
host="${DEPLOY_SSH_HOST:-<primary-node>}"

run_agent() {
  local script="$1"
  ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "$script")"
}

push_file() {
  local source="$1" destination="$2"
  run_agent "umask 077; mkdir -p \"\$HOME/$(dirname "$destination")\"; cat > \"\$HOME/$destination\"; chmod 600 \"\$HOME/$destination\"" < "$source"
}

source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/skills/todo/scripts/todo_confirm_reaction_watch.py" || exit 4

push_file "$repo_root/skills/todo/scripts/todo_confirm_reaction_watch.py" \
  '.hermes/scripts/todo_confirm_reaction_watch.py'
run_agent 'PATH="$HOME/.local/bin:$PATH"; if hermes cron list | grep -Eq "Name:[[:space:]]+todo-confirm-watch$"; then exit 0; fi; hermes cron create "*/1 * * * *" --name todo-confirm-watch --no-agent --script todo_confirm_reaction_watch.py --deliver local'
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list'
