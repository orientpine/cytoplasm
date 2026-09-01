#!/usr/bin/env bash
# PR 을 머지하는 유일한 경로 — 체크가 green 일 때만 머지한다. 머지는 축적이고 배포는 릴리스다.
#
# 왜 필요한가: 이 저장소는 브랜치 보호를 쓸 수 없다(private + Free 는 403). 켤 수 있다 해도
# 에이전트 토큰이 `admin` 이라 기본 설정은 그를 통과시키고, 관리자까지 묶으면
# `automation/land.sh` 의 main 직접 착지가 서버에서 거부된다. 그래서 판정을 서버가 아니라
# 머지 **명령 자체**에 둔다. 2026-08-25 에 두 번 뚫렸다 — PR #267 은 빨간 CI 위에서,
# PR #269 는 체크가 큐잉되기도 전에(`no checks reported`) 머지됐다.
#
# 왜 태그를 자르지 않는가(VA-3, §10-1): 머지=축적, 릴리스=배포. 서명 태그는 소유자 ✅ 1회를
# 받는 `automation/release.sh` 가 자른다(VA-1) — 머지마다 태그를 자르면 그 릴리스 승인이
# 있으나 마나가 된다. 태그 없는 창의 리컨실러는 sha별 사고 통지 대신 3일 임계의 릴리스
# 백로그 다이제스트를 보낸다(deploy_reconcile.reconcile_unsigned_head).
#
# PR 체크는 로컬 영수증이 덮지 못하는 것을 덮는다: GitHub 의 `pull_request` 실행은 **머지 결과
# 트리**를 검사하고, `automation/local_ci.sh` 의 영수증은 브랜치 트리만 검사한다. 2026-08-25
# 에 실제로 갈렸다(머지 결과 43c50a44 vs 영수증 57081383).
#
# 사용:
#   automation/merge-pr.sh <pr-number>
#
# Env:
#   MERGE_PR_ALLOW_UNCHECKED   체크가 **아예 나오지 않을 때만**의 탈출구(예: Actions 결제 중단).
#                              실패한 체크는 이것으로도 통과하지 못한다. 쓰면 stderr 에 남는다.
#   MERGE_PR_GH                default gh (테스트 주입용 이음새)
#   MERGE_PR_POLL_SECONDS                 default 15
#   MERGE_PR_DEADLINE_SECONDS             default 900
#   MERGE_PR_MERGEABILITY_RETRIES         default 3
#   MERGE_PR_MERGEABILITY_POLL_SECONDS    default 2
set -uo pipefail

log() { printf '[merge-pr] %s\n' "$*"; }
die() { printf '[merge-pr] %s\n' "$1" >&2; exit "${2:-1}"; }

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
  || die "not inside a git checkout — run this from the repository"
readonly REPO_ROOT
readonly GH="${MERGE_PR_GH:-gh}"
readonly POLL_SECONDS="${MERGE_PR_POLL_SECONDS:-15}"
readonly DEADLINE_SECONDS="${MERGE_PR_DEADLINE_SECONDS:-900}"
readonly MERGEABILITY_RETRIES="${MERGE_PR_MERGEABILITY_RETRIES:-3}"
readonly MERGEABILITY_POLL_SECONDS="${MERGE_PR_MERGEABILITY_POLL_SECONDS:-2}"

pr="${1:-}"
[[ "$pr" =~ ^[0-9]+$ ]] || die "usage: merge-pr.sh <pr-number>" 2

#: 한 줄 판정으로 접는다. 체크가 0건인 상태는 PENDING 이지 GREEN 이 아니다 — 이름 없는 성공은 없다.
read -r -d '' JUDGE_PY <<'PY' || true
import json
import sys

PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}

payload = json.loads(sys.argv[1])
required = {name for name in sys.argv[2].split(",") if name}
checks = payload.get("statusCheckRollup") or []
mergeable = str(payload.get("mergeable") or "").upper()
missing = sorted(required - {str(one.get("name")) for one in checks})
unsettled = [one for one in checks if one.get("status") != "COMPLETED"]
failing = [
    one for one in checks
    if str(one.get("conclusion") or "").upper() not in PASSING
    and one.get("status") == "COMPLETED"
]


def names(rows):
    return ", ".join(str(one.get("name")) for one in rows)


if payload.get("state") != "OPEN":
    print(f"STATE {payload.get('state')}")
elif payload.get("baseRefName") != "main":
    print(f"BASE {payload.get('baseRefName')}")
elif mergeable == "UNKNOWN":
    print("MERGEABILITY-UNKNOWN")
elif mergeable == "CONFLICTING":
    print("CONFLICT")
elif failing:
    print("RED " + names(failing))
elif missing:
    print("PENDING not reported yet: " + ", ".join(missing))
elif unsettled:
    print("PENDING " + names(unsettled))
else:
    print(f"GREEN {len(checks)} check(s)")
PY

#: 기다려야 할 잡 이름은 워크플로에서 파생한다. 하드코딩하면 잡이 늘 때 조용히 새고, 목록을
#: 안 쓰면 "그 순간 올라와 있던 체크"만 보게 된다 — 2026-08-26 에 체크 1건으로 통과했다.
required_jobs="$(python3 -c '
import sys
from pathlib import Path

import yaml

print(",".join(yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))["jobs"]))
' "$REPO_ROOT/.github/workflows/ci.yml" 2>/dev/null)" \
  || die "cannot read the job list from .github/workflows/ci.yml — refusing to judge"
[[ -n "$required_jobs" ]] || die "the workflow declares no job — refusing to judge"

deadline=$(( SECONDS + DEADLINE_SECONDS ))
mergeability_unknown_attempts=0
while :; do
  view="$("$GH" pr view "$pr" \
    --json state,baseRefName,headRefOid,mergeable,statusCheckRollup 2>/dev/null)" \
    || die "cannot read pull request $pr"
  reading="$(python3 -c "$JUDGE_PY" "$view" "$required_jobs")" \
    || die "cannot judge the checks of pull request $pr"

  case "$reading" in
    GREEN*)
      log "checks ${reading#GREEN } passed"
      break
      ;;
    RED*)
      die "REFUSED — failing check(s): ${reading#RED }. Fix them; the escape hatch does not cover a failure."
      ;;
    STATE*)
      die "REFUSED — pull request $pr is ${reading#STATE }, not OPEN."
      ;;
    BASE*)
      die "REFUSED — pull request $pr targets ${reading#BASE }, not main."
      ;;
    MERGEABILITY-UNKNOWN)
      if (( mergeability_unknown_attempts < MERGEABILITY_RETRIES )); then
        (( mergeability_unknown_attempts += 1 ))
        sleep "$MERGEABILITY_POLL_SECONDS"
        continue
      fi
      die "MERGEABILITY-UNKNOWN — GitHub is still computing whether pull request $pr can merge cleanly. Re-run automation/merge-pr.sh $pr." 4
      ;;
    CONFLICT*)
      die "REFUSED — pull request $pr cannot be merged cleanly. Merge origin/main into the branch, re-run automation/local_ci.sh run, then push."
      ;;
  esac

  if [[ "${MERGE_PR_ALLOW_UNCHECKED:-0}" == "1" ]]; then
    printf '[merge-pr] MERGE_PR_ALLOW_UNCHECKED=1 — merging %s with unsettled checks (%s).\n' \
      "$pr" "${reading#PENDING }" >&2
    break
  fi
  (( SECONDS < deadline )) \
    || die "REFUSED — checks did not settle within ${DEADLINE_SECONDS}s (${reading#PENDING })"
  sleep "$POLL_SECONDS"
done

log "merging pull request $pr"
"$GH" pr merge "$pr" --merge \
  || die "merge failed"

log "landed pull request $pr — 머지는 축적이다. 배포하려면: automation/release.sh (VA-1)"
