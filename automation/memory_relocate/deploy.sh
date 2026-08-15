#!/usr/bin/env bash
# automation/memory_relocate/deploy.sh — deploy the memory-relocation reaction watcher.
#
# Unlike the curator (an isolated top-level runtime package), the relocation watcher
# imports automation.memory_relocate.* / automation.obsidian_write.* / automation.interop.*
# from the repo root, so it RUNS FROM THE OPS CHECKOUT (/srv/autophagy-agents). Deploy =
# (1) provenance-check the local package matches origin/main, (2) ff-pull the ops checkout
# to origin/main so it holds this code, (3) push the no-agent watcher wrapper to
# ~/.hermes/scripts/, (4) idempotently register the reaction cron.
#
# Order is enforced by the provenance guard: commit -> push -> deploy.
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

# Deploy guard: refuse to deploy code that origin/main does not have.
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/memory_relocate" || exit 4

# The watcher runs from the ops checkout — align it to origin/main (read-only ff-pull).
ssh "$host" 'sudo -n -u ops -H git -C /srv/autophagy-agents pull --ff-only'

# No-agent reaction watcher wrapper.
push_file "$repo_root/automation/memory_relocate/cron/memory_relocate_watch.py" '.hermes/scripts/memory_relocate_watch.py'

# Idempotent cron registration (no-agent reaction watcher, every 30m). --all so a paused job is seen.
run_agent 'PATH="$HOME/.local/bin:$PATH"; if hermes cron list --all | grep -Eq "Name:[[:space:]]+memory-relocate-watch$"; then exit 0; fi; hermes cron create "every 30m" --name memory-relocate-watch --no-agent --script memory_relocate_watch.py --deliver local'
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all | grep -A3 memory-relocate-watch || true'
