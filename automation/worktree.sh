#!/usr/bin/env bash
# automation/worktree.sh — 세션 하나에 워크트리 하나. 낡은 기반에서 시작하지 않고,
# 착지하지 못한 일을 지우지 않는다.
#
# WHY 워크트리인가. 여러 세션이 한 워킹트리를 공유하면 같은 파일을 동시에 만진다.
# 2026-08-03 실측: `docs/features.md`를 두 세션이 함께 편집해 한쪽이 남의 묶음 헤더를
# 덮어썼고, 커밋 스테이징에도 남의 미완성 편집이 섞였다. 워크트리는 그 부류를 통째로
# 없앤다 — 브랜치를 대체하는 게 아니라 브랜치가 사는 자리를 분리한다.
# 비용은 작다(실측: 추적 파일 14MB, 생성 0.1초). gitignore된 대용량 잔여물은 따라오지
# 않으므로, 그것이 필요한 작업(configs/rag venv 등)은 메인 체크아웃에서 한다.
#
# WHY fetch 실패가 정지인가. 로컬 `refs/remotes/origin/main`은 fetch 하기 전까지
# 움직이지 않는다. 그래서 fetch 없이 브랜치를 따면 낡은 지점에서 시작한 줄도 모른 채
# 작업하게 되고, 이 리포에는 옛 체크아웃이 최신 결정을 덮어써 배포가 404로 실패한
# 선례가 있다(2026-07-21). "조용히 캐시된 ref 를 쓴다"가 정확히 그 사고의 모양이므로,
# 여기서는 경고가 아니라 거부한다.
#
# WHY 종료에 미착지 검사가 있는가. 이미 머지된 PR 의 브랜치로 증적을 push 하면
# 브랜치에는 올라가지만 착지하지 않는다. push 가 성공하므로 아무 신호도 없다
# (2026-08-03: QA 증적 65줄이 그렇게 사라질 뻔했고, 지우기 전 세어본 덕에 살았다).
# 그 확인을 사람 기억에 맡기지 않는다.
#
# 사용:
#   automation/worktree.sh start <이름> [--paths <경로>...]
#   automation/worktree.sh finish <이름>
#
# Env:
#   WORKTREE_ROOT   워크트리를 둘 곳 (기본: 리포 옆 `autophagy-wt/`)
#   WORKTREE_BASE   기반 브랜치 (기본: main)
set -euo pipefail

MAIN_ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
readonly MAIN_ROOT
readonly WORKTREE_ROOT="${WORKTREE_ROOT:-$(dirname "$MAIN_ROOT")/autophagy-wt}"
readonly BASE="${WORKTREE_BASE:-main}"
readonly REMOTE_REF="refs/remotes/origin/$BASE"
# A bare `session` branch can block the entire `session/*` namespace; override it without changing the default.
readonly BRANCH_PREFIX="${WORKTREE_BRANCH_PREFIX:-session}"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '[worktree] %s\n' "$*"; }
die() { printf '[worktree] ERROR: %s\n' "$1" >&2; exit 1; }

usage() { die "usage: worktree.sh start <name> [--paths <path>...] | finish <name>"; }

# 이름은 경로 조각이 된다 — 슬래시나 상대경로가 섞이면 엉뚱한 곳을 만들거나 지운다.
validate_name() {
  [[ "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$ ]] || die "not a session name: $1"
}

install_session_push_guard() {
  # All linked worktrees share this common hooks directory. install(1) replaces the
  # single target path, so every start refreshes exactly one guard without backups.
  local common_dir hook_source
  common_dir="$(git -C "$MAIN_ROOT" rev-parse --path-format=absolute --git-common-dir)" \
    || die "could not resolve the shared git hooks directory"
  hook_source="$SCRIPT_DIR/hooks/pre-push"
  [[ -f "$hook_source" ]] || die "session push guard is missing: $hook_source"
  install -d "$common_dir/hooks"
  install -m 755 "$hook_source" "$common_dir/hooks/pre-push"
  log "hook: session main-push guard installed at $common_dir/hooks/pre-push"
}

cmd_start() {
  local name="$1"; shift || true
  validate_name "$name"
  install_session_push_guard
  local paths=()
  if [[ "${1:-}" == "--paths" ]]; then
    shift
    while (( $# )); do paths+=("$1"); shift; done
  fi

  local dir="$WORKTREE_ROOT/$name" branch="$BRANCH_PREFIX/$name"
  [[ -e "$dir" ]] && die "$name already has a worktree at $dir"
  git -C "$MAIN_ROOT" rev-parse --verify --quiet "$branch" >/dev/null \
    && die "$name already has a branch ($branch)"

  # 시작점을 정하기 전에 무엇을 놓치고 있었는지 알아야 한다.
  local stale=""
  stale="$(git -C "$MAIN_ROOT" rev-parse --verify --quiet "$REMOTE_REF" || true)"
  git -C "$MAIN_ROOT" fetch --quiet origin "$BASE" 2>/dev/null \
    || die "could not fetch origin/$BASE — refusing to start from a possibly stale base"
  local fresh
  fresh="$(git -C "$MAIN_ROOT" rev-parse --verify "$REMOTE_REF")" \
    || die "origin/$BASE is unavailable after fetch"

  install -d "$WORKTREE_ROOT"
  git -C "$MAIN_ROOT" worktree add --quiet "$dir" -b "$branch" "$fresh" \
    || die "could not create the worktree at $dir"

  log "READY $dir  (branch $branch, base ${fresh:0:12})"
  if [[ -n "$stale" && "$stale" != "$fresh" ]]; then
    log "이 세션을 시작하기 전에 origin/$BASE 가 전진해 있었다 — 방금 따라잡았다:"
    git -C "$MAIN_ROOT" log --oneline --no-decorate "$stale..$fresh" | sed 's/^/    /'
  fi
  if (( ${#paths[@]} )); then
    log "손댈 경로의 최근 이력 — ${paths[*]} (다른 세션이 방금 고쳤는지 보라):"
    git -C "$MAIN_ROOT" log --oneline --no-decorate -8 "$fresh" -- "${paths[@]}" \
      | sed 's/^/    /' || true
    # `[[ ... ]] && ...` 로 쓰면 조건이 거짓일 때 함수 종료코드가 1이 되어 set -e 가
    # 정상 경로를 실패로 만든다. 알림 하나 때문에 시작이 죽으면 안 된다.
    local dirty
    dirty="$(git -C "$MAIN_ROOT" status --porcelain -- "${paths[@]}")"
    if [[ -n "$dirty" ]]; then
      log "주의: 메인 체크아웃에 같은 경로의 미커밋 변경이 있다:"
      printf '%s\n' "$dirty" | sed 's/^/    /'
    fi
  fi
}

cmd_finish() {
  local name="$1"
  validate_name "$name"
  local dir="$WORKTREE_ROOT/$name" branch="$BRANCH_PREFIX/$name"
  [[ -d "$dir" ]] || die "$name has no worktree at $dir"

  local dirty
  dirty="$(git -C "$dir" status --porcelain)"
  if [[ -n "$dirty" ]]; then
    printf '[worktree] ERROR: %s has uncommitted changes — commit or discard them first\n' "$name" >&2
    printf '%s\n' "$dirty" | sed 's/^/    /' >&2
    exit 1
  fi

  # 착지 여부는 방금 가져온 기준으로만 말이 된다.
  git -C "$MAIN_ROOT" fetch --quiet origin "$BASE" 2>/dev/null \
    || die "could not fetch origin/$BASE — cannot tell whether this work landed"
  local unlanded
  unlanded="$(git -C "$MAIN_ROOT" log --oneline --no-decorate "$REMOTE_REF..$branch")"
  if [[ -n "$unlanded" ]]; then
    printf '[worktree] ERROR: %s has commits that never landed on origin/%s — refusing to remove\n' \
      "$name" "$BASE" >&2
    printf '%s\n' "$unlanded" | sed 's/^/    /' >&2
    printf '[worktree]   PR 로 착지시키거나, 정말 버릴 것이면 수동으로 지운다.\n' >&2
    exit 1
  fi

  git -C "$MAIN_ROOT" worktree remove "$dir" || die "could not remove the worktree at $dir"
  git -C "$MAIN_ROOT" branch -d "$branch" >/dev/null \
    || die "could not delete $branch (it should be fully merged by now)"
  log "DONE $name 정리 완료 (원격 브랜치는 건드리지 않았다 — 공유 영향이라 사람이 판단한다)"
}

[[ $# -ge 2 ]] || usage
case "$1" in
  start)  shift; cmd_start "$@" ;;
  finish) shift; cmd_finish "$@" ;;
  *)      usage ;;
esac
