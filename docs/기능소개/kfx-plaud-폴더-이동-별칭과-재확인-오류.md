# 기능 소개: Plaud 폴더 이동 별칭과 클라우드 재확인 오류 보존

**PR:** #501 · **스킬:** `plaud` · **워처:** `plaud_sync_watch.py`

## 무엇을

1. **별칭.** Plaud 폴더 정리로 녹음 id 앞에 `of_`가 붙어도, 접두어를 뺀 id가 같으면 기존 녹음으로 본다. 발견 단계에서 기존 키·녹음 id·`aliases` 목록을 비교하므로 새 레코드나 승인 카드를 만들지 않고, 한 번의 폴에 두 id가 함께 와도 하나만 계획한다. 원본 id는 일괄 변경하지 않아 이미 승인된 해시와 노트 경로가 유지된다.
2. **이미 생긴 중복 병합.** 운영자가 워처에 `--migrate-aliases`로 계획을 먼저 보고, `--apply`를 붙여야만 적용한다. 우선순위는 `written > approved > posted > planned > transcribing > abandoned`이고 같은 상태면 키의 사전순이다. 남는 레코드의 승인 바인딩·본문 경로는 그대로 두고 다른 id는 선택적 `aliases`에 보존한다. 적용은 워처 잠금 안에서 `state.json.bak-<UTC stamp>`에 원본을 먼저 백업한 뒤 상태만 원자적으로 교체한다.
3. **원래 실패 사유 보존.** 백오프 중 클라우드 재확인이 실패해도 그 오류는 `last_recheck_error`에 따로 남고, `last_block_reason`·시도 횟수·재시도 시각은 덮어쓰지 않는다. `plaud 상태`는 두 값을 함께 보여 주고, `status --json`의 `transcribing[]`에는 오류가 있을 때만 `last_recheck_error`가 추가된다.

## 왜

폴더 이동 뒤 같은 녹음이 새 id로 다시 발견되어 승인 카드가 중복됐다. 또 보류 중 클라우드 재확인이 잠깐 실패하면 원래의 전사 실패 사유(예: `rc=5 unsupported-format`)가 `PlaudMcpError`로 덮여 무엇이 문제였는지 알 수 없었다. 두 문제 모두 상태 파일의 정보 손실이라 워처 내부에서 고쳤고, 일반 틱은 병합을 하지 않도록 운영자 명령으로 분리했다.

## 사용 시나리오

### 정상 경로

1. 소유자가 Plaud 앱에서 녹음을 폴더로 옮긴다. 다음 틱에 `of_` 접두어 id가 발견되지만 기존 레코드와 같은 녹음으로 판정되어 아무 카드도 새로 뜨지 않는다.
2. 이미 중복이 생긴 노드에서는 운영자가 워처의 별칭 마이그레이션을 dry-run으로 실행해 `keep=… merge=…` 계획 줄과 `mode=dry-run merges=N`을 확인하고, 문제가 없으면 `--apply`를 붙여 백업 경로가 찍힌 결과를 받는다.
3. 백오프 대기 중 클라우드 조회가 실패하면 저널의 `outcome=retry`는 유지되고, 상태 조회에 원래 사유와 마지막 재확인 오류가 나란히 보인다.

### 실패·거부 경로

- 옛 상태 파일(`aliases`·`last_recheck_error` 없음)은 그대로 읽힌다. 새 필드는 선택적이다.
- 마이그레이션은 폴·전사·승인 처리를 실행하지 않고, 카드·노트·외부 승인 원장을 삭제하지 않는다. 병합할 것이 없으면 `merges=0`으로 끝나고 백업도 만들지 않는다.
- `last_recheck_error`는 마지막 오류 이력이지 현재 클라우드 장애 판정이 아니다. 이 값만 보고 장애를 단정하지 않는다.
- 재확인 오류는 계속 비집계(uncounted)이므로 로컬 실패 횟수를 늘리지 않는다. 폐기 판단은 기존 총 상한 정책 그대로다.

## 승인 경계

승인 카드·민감도 게이트·소유자 ✅는 바뀌지 않았다. 별칭 병합은 운영자가 `--apply`를 명시할 때만 상태 파일을 쓰며, 실제 노드의 상태 병합과 워처 틱 중복 재발견 0 확인은 배포 이후 항목이다.

## 관련

- 스킬: `skills/plaud/SKILL.md` (`폴더 이동 별칭과 클라우드 재확인 오류`)
- 코드: `automation/plaud_sync/aliases.py`, `automation/plaud_sync/model.py`, `automation/plaud_sync/transcribe.py`
- 회귀: `tests/unit/test_plaud_sync_aliases.py`, `tests/unit/test_plaud_sync_alias_cli.py`, `tests/unit/test_plaud_sync_recheck_error.py`
- 검증 증거: `.omo/evidence/kfx/k6-plaud-alias/` (RED 다수 실패 → GREEN 61 passed)
- 참고: [Plaud 전사 정책 게이트](plaud-전사-정책-게이트.md)
