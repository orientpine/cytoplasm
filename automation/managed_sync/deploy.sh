#!/usr/bin/env bash
# automation/managed_sync/deploy.sh — deploy the managed-skill subscriber sync tick.
#
# This is the Hermes no-agent cron path, for a node that already runs Hermes (cha's node
# and any install that prefers cron over systemd). The OTHER deployment of the same tick
# is automation/managed_sync/systemd/, installed as an OPT-IN component by
# `python3 -m automation.install --with-component managed-sync`. Both run the same
# wrapper, so the lock, the credential propagation and the D3 boundary are identical.
#
# Deploy = (1) provenance-check the package matches origin/main, (2) push the no-agent
# wrapper to ~/.hermes/scripts/ under a name unique to this watcher (규약 (e)),
# (3) idempotently register the 30-minute cron. Order is enforced by the provenance
# guard: commit -> push -> deploy.
#
# The wrapper resolves its code through the runtime-root order (release `current` first),
# so there is no ops ff-pull here — the reconciler owns convergence.
#
# NOT deployed by this script: activation. A tick ends at quarantine; mounting a delivered
# release still needs the subscriber's own ✅ (D3).
#
# A node with no subscriber config (~/.hermes/managed-sync/config.json) or no group roster
# (~/.hermes/roster.yaml) fails closed at exit 2 on every tick, with the offending key
# named in the log. That is intended: registering the timer does not silently trust anyone.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-$NODE_DEPLOY_SSH_HOST}"

run_agent() {
  local script="$1"
  ssh "$host" "sudo -n -u $NODE_AGENT_ACCOUNT -H bash -lc $(printf '%q' "$script")"
}

push_file() {
  local source="$1" destination="$2"
  run_agent "umask 077; mkdir -p \"\$HOME/$(dirname "$destination")\"; cat > \"\$HOME/$destination\"; chmod 600 \"\$HOME/$destination\"" < "$source"
}

# Deploy guard: refuse to deploy code that origin/main does not have.
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/managed_sync" || exit 4

# No-agent watcher wrapper. The filename is unique to this watcher (규약 (e)).
push_file "$repo_root/automation/managed_sync/cron/managed_sync_watch.py" '.hermes/scripts/managed_sync_watch.py'

# Idempotent cron registration — a second run must not create a second job.
# --all so a paused job is still seen and not duplicated.
run_agent 'PATH="$HOME/.local/bin:$PATH"; if hermes cron list --all | grep -Eq "Name:[[:space:]]+managed-sync-watch$"; then exit 0; fi; hermes cron create "every 30m" --name managed-sync-watch --no-agent --script managed_sync_watch.py --deliver local'
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all | grep -A3 managed-sync-watch || true'
