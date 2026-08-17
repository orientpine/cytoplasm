# Google Tasks 승인 기반 쓰기

## 무엇을

Google Tasks에 할 일을 추가할 때 owner DM의 **소유자(cha) 승인(✅)**을 거쳐야만 실행되도록 보장한다. 승인 세대는 한 번만 소비하며, 쓰기 직후 API로 다시 조회해 요청한 내용이 정확히 반영됐는지 검증한다.

## 왜

기존에는 에이전트가 터미널 도구로 `gws tasks tasks insert`를 직접 실행할 수 있었다. 이 경로는 외부 효과 게이트의 denylist에 등록되어 있지 않아, 소유자 승인 없이도 외부 시스템에 쓰기가 가능했다. 이로 인해 오타가 섞인 개인 이름이 외부로 유출될 뻔한 사례가 있었다.

이를 해결하기 위해 동결 argv 승인, owner-only 리액션 원장, 경쟁 안전 일회 claim, 성공 보고 전 재조회 검증을 하나의 경로로 묶었다. 재시작 시 `write_started`가 보이면 자동 재삽입하지 않고 조정 대상으로 남긴다. archive 기록 뒤 pending 정리 중 중단돼도 동일한 terminal 전이를 재시도해 정리를 끝내며, 서로 다른 archive로 덮어쓰지는 않는다.

## 사용 시나리오

**happy path — 할 일 추가**

```
cha: "내일 오전 10시에 회의 준비하라고 할 일에 넣어줘"
→ 에이전트: todo_cli request --title "회의 준비" --due "2026-07-31T10:00:00Z"
→ owner DM: "Google Tasks 등록 승인: 회의 준비"
→ cha: ✅ 클릭
→ 워처: 승인 원장 기록 + approved generation archive
→ 에이전트: 같은 argv로 todo_cli create 실행
→ 결과: Tasks 등록 1회 + API 재조회 검증 성공 + receipt 확정
```

**거부 경로 — 승인 없이 실행 시도**

```
에이전트: (승인 레코드 없이) todo_cli create --title "비인가 작업"
→ 결과: TODO-FAIL [4] 승인 레코드가 없습니다.
→ 외부 시스템에 아무런 영향 없음.
```

**재시도 경로 — 이미 소비했거나 중간 상태인 승인**

```
같은 승인으로 create 재실행 → exit 4, 외부 호출 0
insert 뒤 중단되어 write_started가 남음 → exit 7, 자동 재삽입 0
새 request→✅로 새 generation을 만들면 다시 1회 실행 가능
```

## 관련

- 스킬: `todo` — 구현은 [`skills/todo/scripts/todo_cli.py`](../../skills/todo/scripts/todo_cli.py).
- **승인 게이트**: `automation.interop.external_effect_gate`를 재사용하며, `action_hash`는 실행될 전체 인자(argv)에 바인딩된다.
- **검증**: `gws tasks tasks get`으로 저장된 제목과 ID를 대조하여 일치할 때만 성공으로 간주한다.
- 워처/상태: `skills/todo/scripts/todo_confirm_reaction_watch.py`, `todo_approval_store.py`, `todo_approval_store_io.py`, `todo_execution_claim.py`.
- 증적: `docs/qa/RTS-6/04-a1-ssot.txt` ~ `docs/qa/RTS-6/09-review-fixes.txt`.
