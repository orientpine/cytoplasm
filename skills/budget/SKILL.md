---
name: budget
description: "과제비 원장(W0-10 Google Sheet) 조회 + 변경 감지 스킬. `!budget [항목]`은 게이트 없이 즉시 조회. 변경 감지 cron(30분)이 잔액 탭을 SQLite 스냅샷과 diff해 변경 시 규정 요청메일 초안을 만들고, 발송은 반드시 행위 봇의 소유자 DM 승인 메시지에서 cha 본인의 ✅/⛔ 리액션 확인(봇이 두 반응을 미리 추가, 제약 1) 이후에만 일어난다. 승인 표면은 `approval_surface.py` 정책과 초안에 저장된 바인딩으로 결정된다. W4-3."
version: 1.1.1
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Budget, GWS, Approval-Gate, Autophagy]
prerequisites:
  commands: [python3]
---

# 과제비 조회 + 변경 감지 (budget)

cha의 과제비 원장 Sheet를 비공개 런타임 설정 `BUDGET_SHEET_ID`로 지정해 gws CLI(OAuth,
W0-6)로 읽는다. `configs/budget-sheet.md`에는 공개용 형식 예시만 둔다. Sheet는 읽기 전용 —
값 수정 권한은 오너(cha)에게만 있다. 설정이 없으면 조회·감지는 fail-closed로 중단된다.

## 절대 규칙 (안전)

1. **Sheet를 절대 수정하지 마라**: 이 스킬의 모든 경로는 읽기 전용이다.
   `gws sheets ... update/batchUpdate`를 실행하지 않는다.
2. **승인 전 발송 금지**: 요청 메일은 오직 `budget_cli.py confirm`/cron `watch`
   (소유자 ✅/⛔ 리액션 검증 내장, ⛔ 우선)로만 발송된다. `gws gmail +send`를 터미널에서
   직접 실행하지 마라 — pre_tool_call 외부효과 게이트(gws_gmail_send)가 승인
   없는 호출을 차단한다.
3. **금액을 공개 채널에 올리지 마라**: 조회 결과(금액/잔액)는 cha의 DM으로만
   전달한다. 승인 요청 메시지의 금액은 CLI가 자동 마스킹한다.

## 명령 (CLI = `python3 ~/.hermes/skills/budget/scripts/budget_cli.py …`)

### 1) `!budget [항목]` — cha가 DM으로 물어볼 때 (게이트 불요, 즉시)

```bash
python3 ~/.hermes/skills/budget/scripts/budget_cli.py query            # 전체 항목
python3 ~/.hermes/skills/budget/scripts/budget_cli.py query --item 재료비
```

출력의 `ROW {...}` JSON 라인들(항목/예산/집행액/잔액/최종수정)을 읽기 좋은
표로 정리해 cha의 DM으로 답한다. `BUDGET-OK n=…`이 없으면 실패다 — exit 4는
Sheet 접근 실패이니 stderr의 `SHEET-FAIL …` 내용을 그대로 cha에게 보고한다.
`NOT-FOUND`(exit 2)면 알려진 항목 목록을 안내한다.

### 2) 변경 감지 파이프라인 — cron이 자동 수행 (30분, budget-watch)

`snapshot`이 잔액 탭(7행 이후)을 SQLite 이전 스냅샷과 diff하고, 변경이 있으면
규정 요청메일 초안을 만들어 **소유자 DM에 마스킹된 승인 요청을 게시**한다.
같은 변경은 claim 키로 멱등 — 초안이 중복 생성되지 않는다.

게시는 공유 승인 생명주기(`budget_approval` → `automation/interop/approval_lifecycle.py`)를
거치며 승인 키 `budget:{mail_to}`에는 **라이브 승인 메시지가 항상 1건뿐**이다: 같은 초안·같은
해시의 재요청은 아무것도 게시하지 않고, 더 최신 원장 변경이 오면 옛 메시지를 **먼저 삭제한
뒤** 그 초안을 superseded로 내리고 새로 1건만 게시하며, 메시지가 사라졌으면 재게시한다.
cha가 이미 ✅/⛔ 한 요청은 파괴하지 않고 다음 tick이 소비하도록 연기한다. 초안의
`message_id`와 승인 바인딩(`surface`/`channel_id`/`policy_version`)은 이 게이트의 commit만
기록하고, 초안을 읽지 못하면 거부한다(fail-closed). 새 요청은 소유자 DM에 게시되고,
v1·v2에 저장된 기존 초안은 원래 개인 서버 `#approvals`에서 계속 소비된다.
에이전트 턴과 30분 `budget-watch` tick은 `~/.hermes/budget-gate/approval-leases/`의 flock으로
직렬화된다 — 진 쪽은 아무것도 바꾸지 않고 `deferred:lease-held`로 물러난다.

### 3) 반응 확인과 발송 — cha가 소유자 DM 메시지에 ✅/⛔ 리액션

초안 게시 직후 봇이 해당 승인 메시지에 ✅와 ⛔를 이 순서로 미리 추가한다.
cha는 **그 메시지에** ✅로 확정하거나 ⛔로 취소한다. 다음 cron tick의 `watch`가
소유자 반응(봇/타인 거부), 초안 sha256 결합, ⛔ 우선을 독립 재검증한다. ✅만 유효하면
approvals.jsonl 기록 후 `gws gmail +send`를 실행하며, 유효한 ⛔면 초안을 폐기하고
소유자에게 짧게 취소 사실만 알린다.

승인이 없으면 pending 상태이며 **아무것도 발송되지 않는다**. CTA의
`실행/취소 <id>`는 문서화된 대체 수단일 뿐, 기본 확인은 리액션이다. 소유자에게
텍스트 답장이나 별도 텍스트-confirm DM을 요청하지 않는다.

### 4) 상태 확인

```bash
python3 …/budget_cli.py list-drafts    # 초안 목록 (pending/executed)
python3 …/budget_cli.py retry-queue    # Sheet 접근 실패 재시도 큐
```

Sheet 접근 실패는 조용히 사라지지 않는다: 오류가 보고되고 재시도 큐에
적재되며, 다음 성공 tick이 큐를 resolve한다.

## Sandbox scenario

배포 파이프라인용 `scripts/scenario.sh`: 픽스처 Sheet로 query 패리티,
초안 무발송, 마스킹, fail-closed confirm, (어댑터 가용 시) 서명 주입 발송 +
승인 기록 + 발송 로그, Sheet 실패→재시도 큐 왕복을 검증하고
`SCENARIO-PASS`를 출력한다. 네트워크 호출·실시크릿 없음.
