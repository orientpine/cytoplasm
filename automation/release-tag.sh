#!/usr/bin/env bash
# automation/release-tag.sh — `origin/main` 의 현재 HEAD 에 서명된 릴리스 태그를 자른다.
#
# **PR 을 머지한 직후 이것을 실행한다.** 리컨실러는 HEAD 가 서명 태그의 peel 대상일 때만
# 수렴하는데(MD-1), 그 태그를 자르는 코드는 `land.sh` 안에만 있었다. 브랜치 작업은 land 가
# 아니라 PR 머지로 main 에 도달하므로 PR 경로에는 그 단계가 아예 없었고, 그래서 2026-08-20
# 에 PR 6건이 태그 없이 들어가 리컨실러가 132회 연속 실패하고 프로덕션이 얼었다.
#
# 왜 "머지 직후"인가: 태그는 **HEAD 를 정확히 맞혀야** 한다. main 이 그 사이 전진하면 태그는
# 이전 커밋에 남고 노드는 계속 선다 — 2026-08-16 에 main 이 세 번 전진하며 실제로 그랬다.
# 그래서 이 스크립트는 자른 뒤 **HEAD 가 아직 그 sha 인지 다시 확인하고**, 어긋났으면
# 성공으로 끝내지 않는다. 놓친 경우의 조치는 단순하다 — 다시 실행하면 된다(멱등).
#
# 사용:
#   automation/release-tag.sh            # origin/main HEAD 에 태그 (없으면 생성, 있으면 no-op)
#   automation/release-tag.sh --wait     # 노드가 실제로 그 릴리스로 수렴할 때까지 확인
#
# Env:
#   UPDATE_TRUST_SIGNING_KEY   default ~/.ssh/autophagy_update_trust.pub (로컬 전용)
#   DEPLOY_SSH_HOST            --wait 가 조회할 노드 (기본: 노드 config)
#   RELEASE_TAG_WAIT_SECONDS   default 240
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=automation/release_tag_lib.sh
source "$REPO_ROOT/automation/release_tag_lib.sh"

release_tag_block() { release_tag_log "RELEASE-TAG-BLOCK: $1"; exit "${2:-1}"; }

wait_for_convergence() { # wait_for_convergence <sha>
  local sha="$1" host deadline current
  eval "$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env)"
  host="${DEPLOY_SSH_HOST-$NODE_DEPLOY_SSH_HOST}"
  [[ -n "$host" ]] || { release_tag_log "no node host to check — skipping the convergence check"; return 0; }
  deadline=$(( SECONDS + ${RELEASE_TAG_WAIT_SECONDS:-240} ))
  while (( SECONDS < deadline )); do
    current="$(ssh "$host" "readlink $NODE_RELEASE_CURRENT" 2>/dev/null | sed 's|.*/||')"
    if [[ "$current" == "$sha" ]]; then
      release_tag_log "the node converged to $sha"
      return 0
    fi
    sleep 15
  done
  # 못 따라잡은 것이 곧 실패는 아니다 — 리컨실러가 다음 틱에 갈 수도 있다. 다만 조용히
  # 성공이라고 말하지는 않는다. 조용한 성공이 애초에 이 사고를 길게 만든 이유다.
  release_tag_log "the node has not converged yet (last seen ${current:-unknown})"
  release_tag_log "  check: automation/healthcheck.sh, or the reconciler state on the node"
  return 1
}

main() {
  local wait_for_node=0
  case "${1:-}" in
    --wait) wait_for_node=1 ;;
    "") ;;
    *) release_tag_block "usage: release-tag.sh [--wait]" 2 ;;
  esac

  git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || release_tag_block "$REPO_ROOT is not a git checkout"
  git -C "$REPO_ROOT" fetch --quiet origin main --tags \
    || release_tag_block "could not fetch origin"

  local head
  head="$(git -C "$REPO_ROOT" rev-parse origin/main)" \
    || release_tag_block "could not resolve origin/main"

  ensure_signed_tag "$REPO_ROOT" "$head" \
    || release_tag_block "no signed release tag at $head — the reconciler will skip every tick"

  # 사후조건 ①: 태그가 정말 그 커밋으로 peel 되는가(원격에서 다시 읽는다).
  git -C "$REPO_ROOT" fetch --quiet origin --tags || true
  local landed
  landed="$(released_tag_at "$REPO_ROOT" "$head")"
  [[ -n "$landed" ]] \
    || release_tag_block "the tag is not on origin — the reconciler cannot see it"

  # 사후조건 ②: HEAD 가 아직 그 sha 인가. 병렬 세션이 그 사이 머지했으면 태그는 이전
  # 커밋에 남고 노드는 그대로 선다 — 성공으로 보고하면 아무도 모른다.
  local head_now
  head_now="$(git -C "$REPO_ROOT" rev-parse origin/main)"
  if [[ "$head_now" != "$head" ]]; then
    release_tag_log "origin/main moved to ${head_now:0:12} while $landed was being cut for ${head:0:12}"
    release_tag_block "HEAD no longer matches the tag — re-run this command to tag the new head"
  fi

  release_tag_log "released $landed at ${head:0:12}; the reconciler converges within ~2 minutes"
  (( wait_for_node == 0 )) || wait_for_convergence "$head"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
