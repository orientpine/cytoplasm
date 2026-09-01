#!/usr/bin/env bash
# cron 래퍼만 배포한다 — 스킬 자체는 automation/deploy-skill.sh(4단계 게이트)가 마운트한다.
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

# shellcheck source=automation/deploy_push.sh
source "$repo_root/automation/deploy_push.sh"

source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/skills/meeting/scripts/meeting_pending_transcript_watch.py" \
  "$repo_root/skills/meeting/plugin/__init__.py" \
  "$repo_root/skills/meeting/plugin/plugin.yaml" || exit 4

push_file "$repo_root/skills/meeting/scripts/meeting_pending_transcript_watch.py" \
  '.hermes/scripts/meeting_pending_transcript_watch.py'

# 게이트웨이 플러그인은 마운트본이 아니라 **계정 홈 사본**에서 로드된다 — 릴리스 수렴도
# 스킬 마운트도 여기 닿지 않는다. 2026-08-28 실측: 홈 사본이 8/23 자로 5일 낡아 `!meeting`
# 이 이미 없어진 규칙으로 거부됐고, 그 사실을 말해 주는 것이 없었다.
plugin_before="$(run_agent 'sha256sum "$HOME/.hermes/plugins/00-meeting-gate/__init__.py" 2>/dev/null | cut -d" " -f1' < /dev/null)"
push_file "$repo_root/skills/meeting/plugin/__init__.py" \
  '.hermes/plugins/00-meeting-gate/__init__.py'
push_file "$repo_root/skills/meeting/plugin/plugin.yaml" \
  '.hermes/plugins/00-meeting-gate/plugin.yaml'
plugin_after="$(run_agent 'sha256sum "$HOME/.hermes/plugins/00-meeting-gate/__init__.py" | cut -d" " -f1' < /dev/null)"

# 플러그인은 게이트웨이 **프로세스 시작 시** 로드된다. 파일만 밀면 반영되지 않는다.
# 재시동은 자동으로 하지 않는다 — 운영 규칙이 "원인 확인 후에만, agent·peer 함께" 다.
if [ "${plugin_before//[[:space:]]/}" != "${plugin_after//[[:space:]]/}" ]; then
  echo "" >&2
  echo "PLUGIN-CHANGED: 게이트웨이 플러그인이 갱신됐습니다 (${plugin_before:0:12} -> ${plugin_after:0:12})." >&2
  echo "                플러그인은 프로세스 시작 시 로드되므로 **agent·peer 게이트웨이를 함께**" >&2
  echo "                재시동해야 반영됩니다 (docs/guide/operations.md §2)." >&2
fi

# `0 0 * * *` 은 **KST 자정**이다. 노드 TZ 는 Etc/UTC 지만 Hermes 스케줄러가 +09:00 으로
# 해석한다 — 실측(2026-08-28): daily-cost-report 가 `0 9 * * *` 이고 Next run 이
# 2026-08-29T09:00:00+09:00 이다. UTC 로 착각해 9시간 옮기지 마라.
run_agent 'PATH="$HOME/.local/bin:$PATH"; if hermes cron list | grep -Eq "Name:[[:space:]]+meeting-pending-transcript-watch$"; then exit 0; fi; hermes cron create "0 0 * * *" --name meeting-pending-transcript-watch --no-agent --script meeting_pending_transcript_watch.py --deliver discord'
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list | grep -A6 "meeting-pending-transcript-watch" || hermes cron list | tail -20'
