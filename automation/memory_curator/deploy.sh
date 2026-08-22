#!/usr/bin/env bash
# automation/memory_curator/deploy.sh — deploy the memory-curator runtime + cron watcher.
#
# Deploys the WHOLE memory_curator package (preserving the package dir) to
# ~agent/.hermes/memory_curator_runtime/memory_curator/ via tar-over-ssh, so the
# cron wrapper's `sys.path.insert(RUNTIME_DIR); from memory_curator.watch import ...`
# resolves.  tar (not cp) sidesteps the cp-into-existing-dir nesting footgun and the
# `rm -rf` clears stale __pycache__ (e.g. cpython-311 bytecode from an older interp).
#
# Order is enforced by the provenance guard: commit -> push -> deploy.  Only code that
# origin/main already has may reach prod (see automation/deploy_provenance.sh).
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"
host="${DEPLOY_SSH_HOST:-$NODE_DEPLOY_SSH_HOST}"

run_agent() {
  local script="$1"
  ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "$script")"
}

# push_file 은 원격 read-back 으로 착지를 확인한다 — 확인 없는 push 가 rc=0 으로
# 끝나면서 파일은 그대로였던 실측(2026-08-20)이 이 공유 구현의 이유다.
# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"

# Deploy guard: refuse to push code that origin/main does not have.
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/memory_curator" || exit 4

# Runtime package (dir preserved). Exclude bytecode + the cron/ subdir (deployed separately).
tar -C "$repo_root/automation" --exclude='__pycache__' --exclude='memory_curator/cron' -czf - memory_curator \
  | run_agent 'cd "$HOME"; umask 077; rm -rf "$HOME/.hermes/memory_curator_runtime"; mkdir -p "$HOME/.hermes/memory_curator_runtime"; tar -xzf - -C "$HOME/.hermes/memory_curator_runtime"; find "$HOME/.hermes/memory_curator_runtime" -type d -exec chmod 700 {} +; find "$HOME/.hermes/memory_curator_runtime" -type f -name "*.py" -exec chmod 600 {} +'

# Cron watcher wrapper.
push_file "$repo_root/automation/memory_curator/cron/memory_curator_watch.py" '.hermes/scripts/memory_curator_watch.py'

# Idempotent cron registration (no-agent, LLM-free, every 30m). Use --all so a PAUSED
# job is still seen — plain `cron list` hides paused jobs and would duplicate the job.
run_agent 'PATH="$HOME/.local/bin:$PATH"; if hermes cron list --all | grep -Eq "Name:[[:space:]]+memory-curator-watch$"; then exit 0; fi; hermes cron create "every 30m" --name memory-curator-watch --no-agent --script memory_curator_watch.py --deliver local'
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list --all | grep -A3 memory-curator-watch || true'
