# 서명 roster 자동 배포 (W-F2-C)

## 무엇을

관리자가 고정 브랜치 `refs/heads/roster`에 게시한 `roster/roster.yaml`과
`roster/roster.yaml.sig`를 구독자의 평소 managed-sync tick이 자동으로 가져온다.
`autophagy-roster` SSH 서명과 기존 roster 스키마 검증을 모두 통과한 원본 바이트만
`~/.hermes/roster.yaml`에 원자적으로 반영한다.

## 왜

멤버가 바뀔 때마다 관리자가 N명에게 roster 파일을 수동 전달하면 누락과 버전 불일치가 생긴다.
반대로 검증 실패 때 빈 roster로 대체하면 신원 대조가 사라져 stale roster보다 더 위험하다.
그래서 정상 변경은 다음 tick에 전달하되, 어떤 실패도 마지막 정상 roster를 훼손하지 않는다.

## 사용 시나리오

### 정상 경로

1. 관리자가 roster에 새 멤버를 추가하고 `autophagy-roster` namespace로 파일을 서명한다.
2. 두 파일을 관리형 스킬 repo의 `roster` 브랜치에 commit/push한다.
3. 구독자의 다음 자연 cron/systemd tick이 managed skill tag를 먼저 fetch한 뒤, 같은 mirror에 roster ref만 별도로 fetch한다.
4. 출력에 `ROSTER-UPDATED path=...`가 나타나고 로컬 roster가 관리자가 서명한 바이트로 바뀐다.

수동 `python3 -m automation.managed_sync sync`는 기존 skill-only 진단 표면을 유지한다.

스킬 릴리스는 같은 mirror를 쓰지만 별도 tag→sequence→chain→quarantine 경로를 그대로 따른다.
roster 갱신은 스킬을 활성화하지 않으며, 관리형 스킬 MOUNT에는 계속 구독자 본인의 ✅가 필요하다.
Hermes no-agent 런타임이 Python 3.11인 설치에서도 `typing.override` 같은 타입 전용
데코레이터가 import를 막지 않도록 공유 `automation.typing_compat` 경계를 사용한다.

### 실패·거부 경로

- 서명이 없거나, YAML만 서명 뒤 바뀌었거나, 다른 namespace(`git` 등)로 서명하면
  `ROSTER-REJECTED reason=ROSTER-SIGNATURE` 또는 archive 거부 사유를 남긴다.
- 원격에 roster 브랜치가 아직 없으면 `ROSTER-REJECTED reason=ROSTER-FETCH`를 남기고
  기존 roster를 유지한다. 이 거부는 managed skill tag 배달·검증·격리를 실패시키지 않는다.
- 서명이 맞아도 빈 파일·깨진 UTF-8/YAML·스키마 불일치면 거부한다.
- 모든 거부에서 기존 `~/.hermes/roster.yaml`은 byte-for-byte 유지된다. 빈/default roster로 강등하지 않는다.

## 관련

- mirror refspec: `automation/managed_sync/fetch.py`
- archive·검증·원자 교체: `automation/group_roster/fetch.py`
- ordinary tick adapter: `automation/managed_sync/roster_tick.py`, `automation/managed_sync/cli.py`
- 공유 SSH 검증기: `automation/git_tag_signature.py`
- Python 런타임 호환: `automation/typing_compat.py`
- 스키마 경계: `automation/group_roster/parser.py`, `validator.py`
- 회귀: `tests/unit/test_group_roster_fetch.py`, `tests/unit/test_managed_roster_tick.py`,
  `tests/unit/test_managed_fetch.py`, `tests/unit/test_managed_fetch_roster_isolation.py`
- 계획: `.omo/plans/public-release.md` W-F2-C
