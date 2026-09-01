#!/usr/bin/env bash
# Complete an already-approved release from a dedicated workstation worktree.
# This command never creates, retires, or plans an approval request.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_REPO="${RELEASE_COMPLETE_SOURCE_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"
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
exec 9>"$STATE/lock"
flock -n 9 || exit 0
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

head="$(git -C "$WORKTREE" rev-parse HEAD)" || {
  log "HEAD-FAIL: $WORKTREE"
  exit 1
}
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

"${approval[@]}" decision --head "$head"
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
