#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
host="${DEPLOY_SSH_HOST:-<primary-node>}"

run_agent() {
  local script="$1"
  ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "$script")"
}

# push_file 은 원격 read-back 으로 착지를 확인한다 — 확인 없는 push 가 rc=0 으로
# 끝나면서 파일은 그대로였던 실측(2026-08-20)이 이 공유 구현의 이유다.
# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"

source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/skills/budget/scripts/budget_watch.py" || exit 4

push_file "$repo_root/skills/budget/scripts/budget_watch.py" \
  '.hermes/scripts/budget_watch.py'
run_agent 'PATH="$HOME/.local/bin:$PATH"; if hermes cron list | grep -Eq "Name:[[:space:]]+budget-watch$"; then exit 0; fi; hermes cron create "*/30 * * * *" --name budget-watch --no-agent --script budget_watch.py --deliver local'
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list'
