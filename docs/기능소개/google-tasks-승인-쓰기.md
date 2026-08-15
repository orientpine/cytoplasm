# Google Tasks 승인 기반 쓰기

## 무엇을

Google Tasks에 할 일을 추가할 때 **소유자(cha)의 명시적 승인(✅)을 거쳐야만 실행**되도록 보장한다. 또한 쓰기 직후 API로 다시 조회하여 요청한 내용이 정확히 반영되었는지 검증한다.

## 왜

기존에는 에이전트가 터미널 도구로 `gws tasks tasks insert`를 직접 실행할 수 있었다. 이 경로는 외부 효과 게이트의 denylist에 등록되어 있지 않아, 소유자 승인 없이도 외부 시스템에 쓰기가 가능했다. 이로 인해 오타가 섞인 개인 이름이 외부로 유출될 뻔한 사례가 있었다.

이를 해결하기 위해 (1) 모든 쓰기 요청을 동결된 인자와 함께 승인 게이트에 묶고, (2) 성공 보고 전 재조회 검증을 강제하는 불변식을 도입했다.

## 사용 시나리오

**happy path — 할 일 추가**

```
cha: "내일 오전 10시에 회의 준비하라고 할 일에 넣어줘"
→ 에이전트: todo_cli create --title "회의 준비" --due "2026-07-31T10:00:00Z"
→ #approvals: "Google Tasks 쓰기 승인 요청: 회의 준비"
→ cha: ✅ 클릭
→ 결과: Tasks 등록 완료 + API 재조회 검증 성공
```

**거부 경로 — 승인 없이 실행 시도**

```
에이전트: (승인 레코드 없이) todo_cli create --title "비인가 작업"
→ 결과: TODO-FAIL [4] 승인 레코드가 없습니다.
→ 외부 시스템에 아무런 영향 없음.
```

## 관련

- 스킬: `todo` — 구현은 [`skills/todo/scripts/todo_cli.py`](../../skills/todo/scripts/todo_cli.py).
- **승인 게이트**: `automation.interop.external_effect_gate`를 재사용하며, `action_hash`는 실행될 전체 인자(argv)에 바인딩된다.
- **검증**: `gws tasks tasks get`으로 저장된 제목과 ID를 대조하여 일치할 때만 성공으로 간주한다.
- 증적: `docs/qa/RTS-1/ef4-todo.txt`.
