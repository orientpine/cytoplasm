# 기능 소개: 수리(repair) 잔여 결함 7건 정리

**PR:** #515 · **영역:** `automation/repair/` 수리 CLI · 워처 · 작업 클론

## 무엇을

수리 파이프라인에서 남아 있던 일곱 가지 결함을 한 번에 닫았다. 각 항목은 실패하는 회귀를 먼저 남긴 뒤 고쳤다.

| # | 결함 | 지금의 동작 |
| --- | --- | --- |
| 1 | 승인 대기 상태에서 CLI가 모호하게 끝남 | `AWAITING_APPROVAL`은 exit 5로 구분한다. 워처는 레코드를 유지하고 승인을 기록하지 않는다 |
| 2 | 승인한 패치와 적용되는 패치가 다를 수 있음(TOCTOU) | 승인 시 저장한 digest를 Git 적용까지 그대로 전달하고, **바이트 스냅샷 하나**를 stdin으로 `git apply`에 넣는다. 레거시 어댑터도 같은 스냅샷을 쓴다 |
| 3 | 삭제·이름 변경이 검사 범위에서 빠짐 | 기존 diff 파서가 검사 경로와 스테이징 경로를 함께 제공한다 |
| 4 | 승인 내용 불일치를 플래너·샌드박스 실행 뒤에야 확인함 | 승인 바인딩 사전 검사(preguard)가 에이전트 구성 전에 먼저 거부한다 |
| 5 | PR 결과가 보이지 않음 | 열린 PR이 있으면 재사용하고 없으면 만든다. 결과는 `pr_url` 또는 `pr_error`로 명시하고, 자식 프로세스의 stdout/stderr는 비밀을 지운 뒤 전달한다 |
| 6 | 읽기 전용 소비자가 부트스트랩에서 스레드를 만듦 | 전송 바인딩은 `for_pending`까지 미룬다. 게시 경로만 자기 티켓을 명시해 바인딩한다 |
| 7 | 로컬 브랜치가 쌓임 | push와 PR이 성공한 뒤, 카드가 종결 상태이고 병합된 유휴 로컬 ref만 **작업 클론에서** 정리한다. 원격 ref는 건드리지 않는다 |

## 왜

원래 수리 프롬프트의 일곱 항목이 세션 중단으로 남아 있었다. 각각은 작지만 승인 내용과 실제 적용 바이트의 불일치, 읽기 전용 경로의 부작용, 정리되지 않는 ref 등 운영 신뢰에 직접 닿는 문제였다.

## 사용 시나리오

### 정상 경로

1. 수리 티켓의 패치가 승인 카드에 올라간다. 소유자가 ✅하면 승인 시점의 digest와 일치하는 바이트만 작업 클론에 적용되고, 브랜치가 push된 뒤 PR URL이 결과에 실린다. 이미 같은 브랜치의 PR이 열려 있으면 그것을 재사용한다.
2. PR까지 성공하고 카드가 종결되면 다음 정리에서 병합된 로컬 ref가 작업 클론에서 지워진다.
3. 상태만 읽는 소비자(예: 대기 목록 조회)는 Discord 스레드를 만들지 않는다.

### 실패·거부 경로

- 승인 뒤 패치 내용이 바뀌었으면 digest 불일치로 적용을 거부한다.
- 승인 바인딩이 불일치하면 클론 생성·에이전트 구성 이전에 거부되어 비용을 쓰지 않는다.
- `gh`가 없거나 인증되지 않으면 `pr_error`가 명시된다. 자동 PR 게시가 조용히 성공한 척하지 않는다.
- 아직 병합되지 않았거나 체크아웃된 ref는 종결 카드라도 남긴다. 과거에 실패한 게이트 기록은 덮어쓰지 않는다.

## 승인 경계와 한계

- 소유자 ✅ 게이트, 승인 렌더, Discord 전송 규칙은 그대로다.
- 오프라인 회귀는 Discord·LLM·GitHub을 경계에서 대체했고 Git 적용·라이프사이클·셸 스크립트는 실제로 실행했다. **운영 서비스 환경의 `gh` 인증과 과거 미러 ref 정리는 이 테스트로 증명되지 않는다.** 서비스 자격증명 배선은 소유자 롤아웃 작업이다.
- 항목 7은 작업 클론의 로컬 ref만 다룬다. 원격 저장소나 역사적 미러 ref에는 어떤 정리도 하지 않는다.

## 관련

- 코드: `automation/repair/repair_ops_cli.py`, `repair_ops_git.py`, `repair_ops_work_clone.py`, `repair_ops_discord.py`, `repair_ops_reaction_watch.py`
- 회귀: `tests/unit/test_repair_ops_residuals.py`, `test_repair_patch_apply_residuals.py`, `test_repair_approval_preguard.py`, `test_repair_pr_residuals.py`, `test_repair_readonly_bootstrap.py`, `test_repair_branch_pruning.py`, `test_repair_residuals_e2e.py`
- 검증 증거: `.omo/evidence/kfx/r-repair-residuals/` (항목별 red/green, 결합 553 passed)
- 참고: [수리 승인 내용 바인딩](수리-승인-내용-바인딩.md) · [수리 브랜치 반영 경로](수리-브랜치-반영-경로.md)
