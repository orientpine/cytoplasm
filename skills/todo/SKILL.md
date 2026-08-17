---
name: todo
description: Google Tasks 할 일 등록·조회 스킬. 등록(mutate)은 외부효과 승인 게이트를 반드시 경유하고, 쓰기 성공 뒤 tasks.tasks.get 재조회로 저장된 제목·식별자를 검증한다. 조회(list)는 READ이므로 게이트 대상이 아니다. 터미널에서 raw `gws tasks tasks insert`를 직접 실행하지 말 것 — 같은 명령이 denylist 규칙 gws_tasks_mutation에 매칭되어 승인 없이는 차단된다.
version: 1.2.0
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
   승인한다 — 제목이 한 글자라도 다르면 `action_hash`가 달라져 거부된다. `request`는
   공용 승인 lifecycle을 통해 소유자에게 확인 카드를 보내고, 체크아웃 밖 세대형
   pending/archive store에 메시지와 승인 표면 바인딩을 함께 남긴다.
2. **재조회로 증명하지 않으면 성공이라 말하지 않는다.** `insert` 뒤 반드시
   `gws tasks tasks get`으로 다시 읽어 저장된 제목·id가 보낸 값과 같은지 대조한다.
   불일치·빈 응답·재조회 실패는 전부 명시적 실패(비-0 종료)다.
3. **승인 하나는 한 번만 실행한다.** 워처가 ✅를 원장과 불변 archive에 기록하고,
   `create`는 그 generation을 경쟁 안전 claim한 뒤에만 insert한다. 성공 receipt가 있거나
   `write_started`가 남은 generation은 자동 재실행하지 않는다.

## 사용법

```bash
# ① 조회 (READ — 게이트 대상 아님)
python3 ~/.hermes/skills/todo/scripts/todo_cli.py list --tasklist @default

# ② 승인 대상 확인 (READ — 실행하지 않고 hash/target만 계산)
python3 ~/.hermes/skills/todo/scripts/todo_cli.py plan --title "실험 노트 정리"

# ③ 승인 요청 (owner 확인 카드의 유일한 공개 producer)
python3 ~/.hermes/skills/todo/scripts/todo_cli.py request --title "실험 노트 정리" \
  --tasklist @default --notes "합성 메모" --due 2026-08-01T00:00:00Z

# ④ owner DM에서 ✅ 또는 ⛔ 선택 (워처가 원장·archive에 반영)

# ⑤ 등록 (MUTATE — 유효한 미소비 approved generation 필요)
python3 ~/.hermes/skills/todo/scripts/todo_cli.py create --title "실험 노트 정리" \
  --tasklist @default --notes "합성 메모" --due 2026-08-01T00:00:00Z
```

출력 계약:

- `PLAN … external_effect=True approved=<bool> hash=sha256:… target=tool:gws_tasks_mutation:gws`
- `REQUESTED hash=sha256:… target=tool:gws_tasks_mutation:gws`
- `CREATED id=… tasklist=… hash=…` + `VERIFIED reread=tasks.tasks.get id=… title_match=true`
- 실패는 stderr에 `TODO-FAIL …`

종료 코드: `0` 성공 · `3` 게이트 모듈/설정/gws 불가 · `4` 소유자 승인 없음 ·
`5` gws 실행 실패 · `6` 재조회 검증 실패 · `7` write_started 조정 필요.

## 라우팅 규칙 (에이전트용)

- 소유자가 "할 일 추가/투두 등록"을 요청하면 이 스킬의 `create`를 쓴다.
  **`gws tasks tasks insert`를 터미널에서 직접 실행하지 않는다** — 게이트가 차단한다.
- 일정(특정 시각이 있는 약속)은 todo가 아니라 `calendar` 스킬이다. 마감일만 있는
  작업 항목이 todo다. 애매하면 소유자에게 되묻는다(fail-closed).
- 사람·기관 등 개인 고유명사가 제목에 들어가면 쓰기 전에 해석 preflight를 거친다
  (`docs/guide/personal-entity-preflight.md`). `create_task`가 유일한 쓰기 진입점이므로
  가드는 이 함수 하나만 감싸면 된다.

## 배포 경로 및 승인 표면 주의

- 배포된 스킬이 심볼릭 링크인 환경에서는 `Path(__file__).resolve().parents[3]`가
  `/srv/autophagy-skills/releases`를 가리킬 수 있으며, 그 위치에는
  `configs/external-effect-tools.yaml`이 없다. `plan`/`create`가
  `external-effect denylist is unreadable`로 실패하면 먼저
  `/srv/autophagy-agents/configs/external-effect-tools.yaml`의 실제 존재를 확인한 뒤
  `TODO_DENYLIST`로 명시한다. 경로를 추측하거나 fail-open으로 우회하지 않는다.
- 현재 todo CLI에는 승인 메시지 게시·리액션 감시 서브커맨드가 없다. 운영 등록 시
  raw `gws tasks tasks insert`나 수동 승인 레코드 위조로 우회하지 않는다. 동결된
  제목·notes·due의 action hash를 포함한 소유자 DM에 ✅/⛔를 게시하고, 실제 소유자
  리액션과 메시지 해시를 검증한 뒤에만 `external_effect.approval` 레코드를 원자적으로
  기록하고 동일 argv로 `todo_cli.py create`를 실행한다. ⛔가 항상 우선한다.

## 환경 변수

| 변수 | 기본값 | 용도 |
|------|--------|------|
| `TODO_APPROVAL_LOG` | `/srv/autophagy-agents/logs/approvals.jsonl` | 소유자 승인 레코드 원장 |
| `TODO_APPROVAL_ROOT` | `~/.hermes/todo-approvals` | pending/archive 세대·claim/receipt·lifecycle lease/journal |
| `TODO_APPROVAL_TTL` | `86400` | pending 요청 유효 시간(초); 원장 레코드에는 적용하지 않음 |
| `TODO_DENYLIST` | `<repo>/configs/external-effect-tools.yaml` | 외부효과 denylist |
| `TODO_OWNER_ID` | interop config의 `owner_id` | 승인자 판정 |
| `TODO_GWS_BIN` | `which gws` → `~/.local/bin/gws` | gws CLI 경로 |
| `DISCORD_BOT_TOKEN` | 없음 | 확인 카드 게시용 봇 자격증명 |
| `AUTOPHAGY_RUNTIME_ROOT` | 공용 runtime-root 정책 | 게이트 모듈 import 루트(진단: `todo_cli.py runtime-root`) |

## 관련

- 게이트: `automation/interop/external_effect_gate.py` (`load_denylist` / `evaluate_tool_call`)
- denylist 규칙: `configs/external-effect-tools.yaml` → `gws_tasks_mutation`
  (`generic_*` catch-all보다 **위**에 있어야 한다 — 로더는 first-match)
- 테스트: `tests/unit/test_todo_skill.py` · 샌드박스 시나리오: `scripts/scenario.sh` (완전 오프라인)
- 승인 워처: `scripts/todo_confirm_reaction_watch.py` · 설치 정의: `skills/todo/deploy.sh`
- 기능 소개: `docs/기능소개/google-tasks-승인-쓰기.md`
- preflight 계약: `docs/guide/personal-entity-preflight.md`
