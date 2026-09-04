#!/usr/bin/env bash
# automation/release.sh — 릴리스 버전 하나를 소유자 ✅ 로 인가받아 서명 태그를 자른다 (VA-1).
#
# 머지는 축적이고 배포는 릴리스다(§10-1). 이 명령은 clean HEAD == origin/main 에서
# 직전 릴리스 태그..HEAD 의 표면별 변경을 계획하고(release_plan — RC-3 과 같은 선언
# 파서 재사용, 사본 0), 승인 요청을 게시한 뒤 소유자 결정을 계속 기다린다.
#   ✅ → 기존 release_tag_lib 로 서명 태그 컷(리컨실러가 ~2분 내 수렴)
#   ⛔ → 태그 없음, prod 불변, exit 9
#   기본 무응답 → 계속 대기. RELEASE_DEADLINE_SECONDS 를 명시한 운영만 exit 8.
#   프로세스가 죽어도 재실행은 살아 있는 요청을 재사용하고 ✅ 뒤 곧장 태그로 간다.
#
# 워크스테이션 전용이다: 서명키가 여기에만 있고(「릴리스 태그 규칙」), CI·노드로 옮기면
# MD-1 이 막은 escalation 이 부활한다. 태그 이후 release.sh 자체가 리컨실러(①)의
# 수렴을 기다려 `automation/deploy_all.sh --apply --wait-converge`(②~⑦ + 영수증)를
# 호출한다. 필요하면 `--no-deploy`로 태그만 자르고 전량 반영을 따로 재개할 수 있다.
#
# Exit: 0 태그 컷 · 2 usage · 4 전제 미충족 · 8 결정 대기 초과 · 9 소유자 취소 · 10 태그 뒤 전량 반영 미완 · 1 그 외
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${RELEASE_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"

log() { printf '[release] %s\n' "$*" >&2; }
die() { log "RELEASE-BLOCK: $1"; exit "${2:-1}"; }

usage="usage: release.sh [--no-deploy] [--bump {major,minor,patch}]"
no_deploy=0
bump=patch
while (( $# )); do
  case "$1" in
    --no-deploy) no_deploy=1; shift ;;
    --bump)
      (( $# >= 2 )) || { echo "$usage" >&2; exit 2; }
      case "$2" in major|minor|patch) bump="$2" ;; *) echo "$usage" >&2; exit 2 ;; esac
      shift 2
      ;;
    --help|-h) echo "$usage"; exit 0 ;;
    *) echo "$usage" >&2; exit 2 ;;
  esac
done

# shellcheck source=automation/release_tag_lib.sh
source "${RELEASE_TAG_LIB:-$SCRIPT_DIR/release_tag_lib.sh}"

if [[ -n "${RELEASE_APPROVAL_CMD:-}" ]]; then
  read -r -a approval <<< "$RELEASE_APPROVAL_CMD"
  plan_approval=("${approval[@]}")
else
  approval=("$SCRIPT_DIR/release_approval_remote.sh")
  # The repository exists only on the workstation.  Discord credentials and
  # gate state are remote, but diff planning must stay beside this checkout.
  plan_approval=(python3 -m automation.release_approval)
fi
local_ci="${RELEASE_LOCAL_CI:-$SCRIPT_DIR/local_ci.sh}"

# 전제 ①: clean tree — 검사·승인받는 것과 태그되는 것이 같은 바이트여야 한다.
dirty="$(git -C "$REPO_ROOT" status --porcelain=v1 --untracked-files=no \
  -- . ':(exclude).omo/senpi-task')" || die "cannot read the working tree" 4
[[ -z "$dirty" ]] || die "tracked files are modified — 릴리스는 커밋된 트리에서만 자른다" 4

# 전제 ②: HEAD == origin/main — 리컨실러가 수렴하는 것은 origin/main 뿐이다.
git -C "$REPO_ROOT" fetch --quiet origin main --tags || die "could not fetch origin" 4
head="$(git -C "$REPO_ROOT" rev-parse HEAD)" || die "cannot resolve HEAD" 4
main="$(git -C "$REPO_ROOT" rev-parse origin/main)" || die "cannot resolve origin/main" 4
[[ "$head" == "$main" ]] \
  || die "HEAD ${head:0:12} != origin/main ${main:0:12} — 릴리스는 origin/main 에서만 자른다" 4

# 전제 ③: 로컬 CI 영수증 — 기존 push 게이트의 판정을 그대로 재사용한다.
bash "$local_ci" verify "$head" || die "no valid local CI receipt for ${head:0:12}" 4

version="$(release_version_for "$REPO_ROOT" "$head" "$bump")" || die "could not derive the next version" 4
base="$(latest_release_base "$REPO_ROOT")"
if [[ -z "$base" ]]; then
  base="$(git -C "$REPO_ROOT" rev-list --max-parents=0 HEAD | tail -n 1)" \
    || die "cannot resolve the first release base" 4
fi

poll_decision() { # poll_decision → 전역 rc 로 소유자 결정을 돌려준다
  "${approval[@]}" decision --head "$head"
  decision_rc=$?
}

post_request() { # post_request → 요청을 게시한다. stderr 는 붙잡되 언제나 그대로 되울린다.
  "${approval[@]}" request --plan-file "$workdir/plan.json" \
    > "$workdir/record.json" 2> "$workdir/request.stderr"
  local rc=$?
  cat "$workdir/request.stderr" >&2   # 소유자 안내 줄은 성공·실패 모두 터미널에 닿아야 한다
  return "$rc"
}

# 세션이 죽은 뒤의 재실행: 이미 ✅ 된 요청이 살아 있으면 다시 게시하지 않고 태그로 간다.
poll_decision
if (( decision_rc != 0 )); then
  workdir="$(mktemp -d)" || die "mktemp failed" 1
  trap 'rm -rf -- "$workdir"' EXIT
  "${approval[@]}" retire --head "$base" \
    || die "previous release record cannot be archived safely" 4
  "${plan_approval[@]}" plan --repo "$REPO_ROOT" --base "$base" --head "$head" \
    --version "$version" --bump "$bump" > "$workdir/plan.json" || die "release plan failed" 4
  request_refusal="approval request was refused — 살아 있는 다른 요청은 파괴하지 않는다 (⛔ 로 막혔다면 노드 agent 계정에서: python3 -m automation.release_abandon --version <v> --head <sha> --message-id <id> --reason <why>)"
  if ! post_request; then
    # 낡은 pending 요청의 자동 복구. 두 세션이 번갈아 릴리스하면, 소유자 결정을 아직
    # 기다리는(bound_pending) 요청이 이미 지나간 커밋에 묶인 채 남아 이후 모든 요청을
    # binding-mismatch 로 죽인다(2026-08-31 v1.0.141@6a03321f 실측 — 사람이 노드에서
    # release_abandon 을 돌려야 풀렸다).
    #
    # 후속 과제 항목은 staleness 를 'head 가 origin/main 의 조상이 아님'으로 적었지만,
    # 사건의 head 6a03321f 는 origin/main 의 조상이었다. 다시 릴리스될 수 있는 것은
    # 지금의 origin/main tip 하나뿐이므로(전제 ② + main 은 ff-only), tip 불일치가
    # 충실한 조건이다 — 조상 여부는 낡은 요청을 낡지 않았다고 잘못 말한다.
    stale_version=""; stale_head=""; stale_message_id=""; stale_probe=""
    if grep -q 'reason=binding-mismatch' "$workdir/request.stderr"; then
      stale_line="$(grep -m1 '^RELEASE-REQUEST-STALE: ' "$workdir/request.stderr" || true)"
      if [[ -n "$stale_line" ]]; then
        read -r -a stale_fields <<< "${stale_line#RELEASE-REQUEST-STALE: }"
        for stale_field in "${stale_fields[@]}"; do
          case "$stale_field" in
            version=*) stale_version="${stale_field#version=}" ;;
            head=*) stale_head="${stale_field#head=}" ;;
            message_id=*) stale_message_id="${stale_field#message_id=}" ;;
            probe=*) stale_probe="${stale_field#probe=}" ;;
          esac
        done
      fi
    fi
    recovered=0
    if [[ "$stale_probe" == "bound_pending" && -n "$stale_version" && -n "$stale_head" \
          && -n "$stale_message_id" && "$stale_head" != "$main" ]]; then
      if "${approval[@]}" abandon --version "$stale_version" --head "$stale_head" \
        --message-id "$stale_message_id" \
        --reason "stale pending release superseded by origin/main advance (release.sh auto-recovery)"; then
        log "abandoned stale pending release $stale_version at ${stale_head:0:12} — 요청을 한 번만 다시 게시한다"
        post_request && recovered=1   # 재시도는 정확히 한 번이다 — 루프는 없다
      fi
    fi
    (( recovered == 1 )) || die "$request_refusal" 1
  fi
  log "approval requested: $version at ${head:0:12}"

  deadline_seconds="${RELEASE_DEADLINE_SECONDS:-}"
  [[ -z "$deadline_seconds" || "$deadline_seconds" =~ ^[0-9]+$ ]] \
    || die "RELEASE_DEADLINE_SECONDS must be an unsigned integer" 2
  deadline=0
  [[ -z "$deadline_seconds" ]] || deadline=$(( SECONDS + deadline_seconds ))
  transient_failures=0
  while :; do
    poll_decision
    case "$decision_rc" in
      0) break ;;
      9) die "owner cancelled $version — 태그 없음, prod 불변" 9 ;;
      7) transient_failures=0 ;;
      2) die "release request disappeared or was rebound (rc=2)" 1 ;;
      *)
        # rc=255 등은 SSH 불통 같은 일시 실패일 수 있다 — 한 번에 죽지 않고
        # 연속 10회까지 재시도한다(2026-08-31 실측: 일시 255 가 대기를 죽였다).
        transient_failures=$(( transient_failures + 1 ))
        (( transient_failures < 10 )) \
          || die "release request is no longer decidable (rc=$decision_rc, ${transient_failures}회 연속)" 1
        log "decision poll failed (rc=$decision_rc); retrying (${transient_failures}/10)"
        ;;
    esac
    if [[ -n "$deadline_seconds" ]] && (( SECONDS >= deadline )); then
      log "no owner decision within the window — 요청은 살아 있고, 재실행이 곧 재개다"
      exit 8
    fi
    sleep "${RELEASE_POLL_SECONDS:-15}"
  done
else
  log "approved release request already live for ${head:0:12} — resuming to the tag cut"
fi

ensure_signed_tag "$REPO_ROOT" "$head" "$version" || die "signed release tag failed" 1
log "released $version at ${head:0:12} — 리컨실러가 ~2분 내 수렴한다"
if (( no_deploy )); then
  log "수렴 후 전량 반영·영수증: automation/deploy_all.sh --apply"
  exit 0
fi
# 태그는 잘렸다 — 여기서부터의 실패는 태그를 되돌리지 않는다. 노드는 리컨실러로 수렴하고,
# 전량 반영은 재실행(release.sh 또는 deploy_all.sh --apply)이 이어받는다.
"${RELEASE_DEPLOY_ALL:-$SCRIPT_DIR/deploy_all.sh}" --apply --wait-converge
rc=$?
(( rc == 0 )) \
  || die "full deployment did not complete (deploy_all rc=$rc) — 태그는 잘렸고 릴리스 트리는 수렴한다; 재실행이 재개다 (automation/release.sh 또는 automation/deploy_all.sh --apply)" 10
log "fully deployed $version at ${head:0:12} — receipt written by deploy_all"
