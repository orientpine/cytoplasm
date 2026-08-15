---
name: todo
description: Google Tasks 할 일 등록·조회 스킬. 등록(mutate)은 외부효과 승인 게이트를 반드시 경유하고, 쓰기 성공 뒤 tasks.tasks.get 재조회로 저장된 제목·식별자를 검증한다. 조회(list)는 READ이므로 게이트 대상이 아니다. 터미널에서 raw `gws tasks tasks insert`를 직접 실행하지 말 것 — 같은 명령이 denylist 규칙 gws_tasks_mutation에 매칭되어 승인 없이는 차단된다.
version: 1.0.0
license: proprietary
metadata:
  hermes:
    tags: [tasks, todo, external-effect, approval-gate, gws]
---

# todo — 승인 게이트 경유 Google Tasks

## 이 스킬이 존재하는 이유

Google Tasks 쓰기에는 **repo 측 코드가 아예 없었다.** 에이전트가 터미널 도구로
`gws tasks tasks insert`를 치면 denylist 어느 규칙에도 매칭되지 않아 프로덕션 게이트가
이를 *읽기*로 분류하고 그대로 통과시켰다. 그 경로로 잘못 옮겨 적은 개인 고유명사가
소유자 ✅ 없이 외부 시스템에 기록됐다.

이 스킬은 그 구멍을 두 겹으로 막는다.

1. **승인 레코드 없으면 쓰지 않는다.** 실행할 argv를 먼저 동결하고 기존
   `automation/interop/external_effect_gate.py`에 그대로 넘긴다. ✅ 하나는 argv 하나만
   승인한다 — 제목이 한 글자라도 다르면 `action_hash`가 달라져 거부된다.
   **새 승인 표면·워처·resolver를 만들지 않는다.** 기존 게이트 레코드가 유일한 권위다.
2. **재조회로 증명하지 않으면 성공이라 말하지 않는다.** `insert` 뒤 반드시
   `gws tasks tasks get`으로 다시 읽어 저장된 제목·id가 보낸 값과 같은지 대조한다.
   불일치·빈 응답·재조회 실패는 전부 명시적 실패(비-0 종료)다.

## 사용법

```bash
# ① 조회 (READ — 게이트 대상 아님)
python3 ~/.hermes/skills/todo/scripts/todo_cli.py list --tasklist @default

# ② 승인 대상 확인 (READ — 실행하지 않고 hash/target만 계산)
python3 ~/.hermes/skills/todo/scripts/todo_cli.py plan --title "실험 노트 정리"

# ③ 등록 (MUTATE — 소유자 승인 레코드 필요)
python3 ~/.hermes/skills/todo/scripts/todo_cli.py create --title "실험 노트 정리" \
  --tasklist @default --notes "합성 메모" --due 2026-08-01T00:00:00Z
```

출력 계약:

- `PLAN … external_effect=True approved=<bool> hash=sha256:… target=tool:gws_tasks_mutation:gws`
- `CREATED id=… tasklist=… hash=…` + `VERIFIED reread=tasks.tasks.get id=… title_match=true`
- 실패는 stderr에 `TODO-FAIL …`

종료 코드: `0` 성공 · `3` 게이트 모듈/설정/gws 불가 · `4` 소유자 승인 없음 ·
`5` gws 실행 실패 · `6` 재조회 검증 실패.

## 라우팅 규칙 (에이전트용)

- 소유자가 "할 일 추가/투두 등록"을 요청하면 이 스킬의 `create`를 쓴다.
  **`gws tasks tasks insert`를 터미널에서 직접 실행하지 않는다** — 게이트가 차단한다.
- 일정(특정 시각이 있는 약속)은 todo가 아니라 `calendar` 스킬이다. 마감일만 있는
  작업 항목이 todo다. 애매하면 소유자에게 되묻는다(fail-closed).
- 사람·기관 등 개인 고유명사가 제목에 들어가면 쓰기 전에 해석 preflight를 거친다
  (`docs/guide/personal-entity-preflight.md`). `create_task`가 유일한 쓰기 진입점이므로
  가드는 이 함수 하나만 감싸면 된다.

## 환경 변수

| 변수 | 기본값 | 용도 |
|------|--------|------|
| `TODO_APPROVAL_LOG` | `/srv/autophagy-agents/logs/approvals.jsonl` | 소유자 승인 레코드 원장 |
| `TODO_DENYLIST` | `<repo>/configs/external-effect-tools.yaml` | 외부효과 denylist |
| `TODO_OWNER_ID` | interop config의 `owner_id` | 승인자 판정 |
| `TODO_GWS_BIN` | `which gws` → `~/.local/bin/gws` | gws CLI 경로 |
| `AUTOPHAGY_REPO_ROOT` | 스크립트 기준 상위 3단계 | 게이트 모듈 import 루트 |

## 관련

- 게이트: `automation/interop/external_effect_gate.py` (`load_denylist` / `evaluate_tool_call`)
- denylist 규칙: `configs/external-effect-tools.yaml` → `gws_tasks_mutation`
  (`generic_*` catch-all보다 **위**에 있어야 한다 — 로더는 first-match)
- 테스트: `tests/unit/test_todo_skill.py` · 샌드박스 시나리오: `scripts/scenario.sh` (완전 오프라인)
- preflight 계약: `docs/guide/personal-entity-preflight.md`
