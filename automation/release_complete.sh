#!/usr/bin/env bash
# Complete an already-approved release from a dedicated workstation worktree.
# This command never creates, retires, or plans an approval request.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_REPO="${RELEASE_COMPLETE_SOURCE_REPO:-$REPO_ROOT}"
STATE="${RELEASE_COMPLETE_STATE:-$HOME/.hermes/release-completer}"
WORKTREE="${RELEASE_COMPLETE_WORKTREE:-$STATE/worktree}"

log() { printf '[release-complete] %s\n' "$*"; }
usage() { printf 'usage: release_complete.sh\n'; }

case "${1:-}" in
  "") ;;
  --help|-h) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

umask 077
mkdir -p -- "$STATE" || { log "STATE-FAIL: $STATE"; exit 1; }
chmod 0700 "$STATE" || { log "STATE-FAIL: $STATE"; exit 1; }
# 자기 갱신(SELF-UPDATE) 뒤의 재진입은 잠금 fd 9 를 exec 너머로 물려받는다 — 같은 파일을 다시
# 열면 새 open file description 이라 잠금이 풀리므로, fd 가 실제로 열려 있을 때만 그 잠금을 믿는다.
if [[ "${RELEASE_COMPLETE_REEXEC:-}" == 1 ]] && { true >&9; } 2>/dev/null; then
  :
else
  exec 9>"$STATE/lock"
  flock -n 9 || exit 0
fi
export GIT_TERMINAL_PROMPT=0

if [[ ! -d "$WORKTREE" ]]; then
  if ! git -C "$SOURCE_REPO" fetch --quiet origin main --tags; then
    log "FETCH-FAIL"
    exit 1
  fi
  if ! git -C "$SOURCE_REPO" worktree add --detach "$WORKTREE" origin/main; then
    log "WORKTREE-FAIL: $WORKTREE"
    exit 1
  fi
else
  if ! git -C "$WORKTREE" fetch --quiet origin main --tags; then
    log "FETCH-FAIL"
    exit 1
  fi
fi

dirty="$(git -C "$WORKTREE" status --porcelain=v1 --untracked-files=no)" || {
  log "COMPLETER-STATUS-FAIL: $WORKTREE"
  exit 1
}
if [[ -n "$dirty" ]]; then
  log "COMPLETER-DIRTY: $WORKTREE — 이 워크트리는 completer 전용이다; 손대지 말고 지우면 다음 틱이 다시 만든다"
  exit 5
fi
if ! git -C "$WORKTREE" checkout --quiet --detach origin/main; then
  log "CHECKOUT-FAIL: $WORKTREE"
  exit 1
fi

# 자기 갱신 — 유닛의 ExecStart 는 메인 체크아웃의 이 파일을 가리키지만, 매 틱 origin/main 을
# 따르는 것은 워크트리뿐이다. 방금 맞춘 워크트리의 사본이 지금 도는 나와 다르면 그 사본으로
# exec 한다(잠금 fd 9 상속, RELEASE_COMPLETE_REEXEC 재진입 가드). 그래서 이 스크립트 자체의
# 변경도 메인 체크아웃 ff-pull 없이 다음 틱부터 반영된다(2026-09-05 실측: 두 틱이 옛 사본으로 돌았다).
fresh="$WORKTREE/automation/release_complete.sh"
self="$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")"
if [[ "${RELEASE_COMPLETE_REEXEC:-}" != 1 && -f "$fresh" ]] && ! cmp -s "$self" "$fresh"; then
  log "SELF-UPDATE: origin/main 의 release_complete.sh 가 이 사본과 다르다 — 워크트리 사본으로 exec"
  export RELEASE_COMPLETE_REEXEC=1
  exec bash "$fresh" "$@"
  log "SELF-UPDATE-FAIL: exec 실패 — 이 사본으로 계속"
fi

head="$(git -C "$WORKTREE" rev-parse HEAD)" || {
  log "HEAD-FAIL: $WORKTREE"
  exit 1
}
# 적용 완료 DM 통지 스윕 — **완결 단축회로보다 앞**이어야 한다. 통지 대상은 이미
# `completed/<sha>` 가 있는 릴리스이므로 단축회로 뒤에 두면 영영 도달하지 못한다.
# 판정·발신은 전부 파이썬에 있다(bash 에는 배선만). 실패해도 완결을 막지 않는다.
# cd 는 장식이 아니다 — `python3 -m` 은 sys.path[0] 에 cwd 를 넣으므로, 유닛의
# WorkingDirectory(메인 체크아웃)에 있는 낡은 automation 패키지가 self-update 로 갈아탄
# 워크트리 세대의 모듈을 가린다(2026-09-09 실측: 매 틱 No module named ...).
(cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT" python3 -m automation.release_applied_notice \
  sweep --state "$STATE" --repo "$WORKTREE") || true

# 이미 인가된 릴리스의 완결 리컨실 — 완결 단축회로보다 **앞**이어야 한다. origin/main 이
# 태그를 앞지르면 아래 결정은 매 틱 HEAD 불일치로 서므로, 노드가 실제로 돌리는 릴리스의
# 남은 완결(전량 반영 + 마커)이 여기서 재개되지 않으면 영영 재개되지 않는다. 이 경로는
# release.sh 를 부르지 않아 태그를 자르지 않으므로 새 인가를 만들지 않는다.
reconcile_authorized_release() {
  local target sha version attempts_file attempts max deploy_command rc
  local -a deploy_cmd
  target="$(cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT" python3 -m automation.release_completion_target \
    --repo "$WORKTREE" --state "$STATE")" || return 0
  sha="${target%% *}"
  version="${target##* }"
  attempts_file="$STATE/attempts/reconcile-$sha"
  attempts="$(cat "$attempts_file" 2>/dev/null || printf 0)"
  max="${RELEASE_COMPLETE_MAX_ATTEMPTS:-3}"
  if (( attempts >= max )); then
    log "RECONCILE-GIVEUP ${sha:0:12} after $attempts attempts — automation/deploy_all.sh --apply 를 손으로 재실행"
    return 0
  fi
  if ! git -C "$WORKTREE" checkout --quiet --detach "$sha"; then
    log "RECONCILE-CHECKOUT-FAIL ${sha:0:12}"
    return 0
  fi
  log "reconciling approved $version (${sha:0:12}) — origin/main 이 그 뒤로 전진했다"
  deploy_command="${RELEASE_COMPLETE_DEPLOY_CMD:-$WORKTREE/automation/deploy_all.sh}"
  read -r -a deploy_cmd <<< "$deploy_command"
  ( cd "$WORKTREE" && "${deploy_cmd[@]}" --apply )
  rc=$?
  git -C "$WORKTREE" checkout --quiet --detach origin/main \
    || log "RECONCILE-RESTORE-FAIL: 워크트리를 origin/main 으로 되돌리지 못했다"
  case "$rc" in
    0)
      rm -f -- "$attempts_file"
      if mkdir -p -- "$STATE/completed" \
        && printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATE/completed/$sha"; then
        log "reconciled $version (${sha:0:12})"
      else
        log "MARKER-FAIL: $STATE/completed/$sha"
      fi
      ;;
    4)
      # 노드가 아직 그 릴리스가 아니다(RELEASE-MISMATCH) — 스스로 낫는 전이 상태라 시도로
      # 세지 않는다. 세면 2분 틱 × 3 = 6분 만에 영구 포기가 되는데, 2026-09-08 실측 수렴은
      # 52분 걸렸다.
      log "RECONCILE-DEFER ${sha:0:12} — 노드 릴리스가 아직 그 sha 가 아니다"
      ;;
    *)
      mkdir -p -- "$STATE/attempts" \
        && printf '%s\n' "$(( attempts + 1 ))" > "$attempts_file"
      log "RECONCILE-FAIL rc=$rc ${sha:0:12} — 재실행이 재개다(다음 틱)"
      ;;
  esac
}

reconcile_authorized_release

marker="$STATE/completed/$head"
[[ -f "$marker" ]] && exit 0

if [[ -n "${RELEASE_APPROVAL_CMD:-}" ]]; then
  read -r -a approval <<< "$RELEASE_APPROVAL_CMD"
else
  RELEASE_APPROVAL_CMD="$WORKTREE/automation/release_approval_remote.sh"
  read -r -a approval <<< "$RELEASE_APPROVAL_CMD"
fi
export RELEASE_APPROVAL_CMD

release_command="${RELEASE_COMPLETE_RELEASE_CMD:-$WORKTREE/automation/release.sh}"
read -r -a release_cmd <<< "$release_command"

# 결정에는 **잘린 릴리스 sha** 를 함께 준다. 그 사실이 없으면 결정 경로는 이미 실행된
# 릴리스를 낡은 승인으로 오인해 소유자에게 거짓 ⛔("…가 origin/main …와 달라 자동 완결할
# 수 없습니다")를 보낸다 — 2026-09-10 v1.6.7 실측: 적용 완료 통지가 이미 나간 릴리스인데
# 팁이 그 뒤로 전진했다는 이유만으로 경고가 갔다. 판정은 release_approval 이 단독으로 하고
# 여기서는 사실만 나른다 — 태그 해석 사본을 bash 에 만들지 않는다.
tagged_release="$(cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT" \
  python3 -m automation.release_completion_target \
  --repo "$WORKTREE" --state "$STATE" --print-tagged 2>/dev/null)" || tagged_release=""
decision_argv=(decision --head "$head" --notify-stale)
[[ -n "$tagged_release" ]] && decision_argv+=(--tagged "$tagged_release")
"${approval[@]}" "${decision_argv[@]}"
decision_rc=$?
case "$decision_rc" in
  0)
    # 시도 상한은 sha 별이다 — deploy-skill.sh 의 백오프와 같은 원리로, 지속 결함(예:
    # SANDBOX-BLOCK)을 매 틱 전량 재배포로 되풀이하지 않는다. 원인을 고쳐 랜딩하면
    # 새 sha 가 새 릴리스가 되어 상한이 처음부터 다시 센다. 손으로 재개하려면
    # automation/release.sh 를 직접 돌린다(그 경로는 이 상한을 모른다).
    attempts_file="$STATE/attempts/$head"
    attempts="$(cat "$attempts_file" 2>/dev/null || printf 0)"
    max_attempts="${RELEASE_COMPLETE_MAX_ATTEMPTS:-3}"
    if (( attempts >= max_attempts )); then
      log "COMPLETE-GIVEUP ${head:0:12} after $attempts attempts — 원인을 고쳐 새 릴리스를 자르거나 automation/release.sh 를 손으로 재실행"
      exit 0
    fi
    log "approved release live for ${head:0:12} — completing (attempt $(( attempts + 1 ))/$max_attempts)"
    (
      cd "$WORKTREE" || exit 1
      export RELEASE_REPO_ROOT="$WORKTREE"
      "${release_cmd[@]}"
    )
    release_rc=$?
    if (( release_rc != 0 )); then
      mkdir -p -- "$STATE/attempts" && printf '%s\n' "$(( attempts + 1 ))" > "$attempts_file"
      log "COMPLETE-FAIL rc=$release_rc for ${head:0:12} — 재실행이 재개다(다음 틱)"
      exit "$release_rc"
    fi
    rm -f -- "$attempts_file"
    mkdir -p -- "$STATE/completed" || { log "MARKER-FAIL: $marker"; exit 1; }
    printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$marker" \
      || { log "MARKER-FAIL: $marker"; exit 1; }
    log "completed ${head:0:12}"
    ;;
  7) log "pending ${head:0:12}" ;;
  2) exit 0 ;;
  9) log "cancelled ${head:0:12}" ;;
  *) log "decision unavailable (rc=$decision_rc) — 다음 틱" ;;
esac
exit 0
