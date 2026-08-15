---
name: coordination
description: "에이전트간 일정 조율 스킬 (W3-3). cha가 'OO님과 미팅 잡아줘'라고 하면 상대 에이전트에 §2 가용시간 질의 → 교집합 후보 ≤3개를 조회한다. 공개 v1에서는 상대 소유자 승인 경로가 없어 자동 합의·캘린더 등록을 하지 않으며, 실제 양측 승인 흐름은 W-F2.5-D(v2) 범위다. §2 조율 봉투(envelope)는 #autophagy-agents(인터롭 채널)에서 오가며, #team에는 간결한 확정 통지만 게시된다. 10분 무응답/후보 0개는 에스컬레이션 DM 후 종료(캘린더 쓰기 0건). 거절은 재협상 1회 후 종료. 조율은 피어+시간 범위(예: '오전') 요청 전용 — 피어가 명시돼도 '정확한 단일 시각'이면 본인 단독 일정이므로 request 진입 즉시 ROUTING-REJECT(exit 2)로 calendar로 되돌린다(피어 질의 전 차단). 상대 미지정 요청은 calendar 소유."
version: 1.1.0
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Coordination, Interop, Calendar, Approval-Gate, Autophagy]
prerequisites:
  commands: [python3]
---

# 에이전트간 일정 조율 (coordination)

cha의 "OO님과 미팅 잡아줘" 요청을 인터롭 규약 §2.3(조율 프로토콜)으로 처리한다.

## 절대 규칙 (안전)

1. **양측 승인 전 캘린더 변경 금지**: 캘린더 쓰기는 오직 W3-1 calendar 스킬의
   confirm 게이트를 통해서만 일어난다. `gws calendar events …` 직접 실행 금지
   (pre_tool_call 외부효과 게이트가 차단).
2. **#team에는 간결한 확정 통지만**: §2 조율 봉투(envelope)는 #autophagy-agents(인터롭 채널)에서 교환하며, #team에는 액션 아이템/지시/멘션/질문을 게시하지 마라(봇 캐스케이드 위험 — W2-3 사고). 상세 결과는 cha DM으로.
3. **일정 제목 등 캘린더 내용은 공개 채널에 게시하지 않는다** (§2 봉투 payload에도
   제목을 넣지 않는다 — slot 시각만).
4. **Deadlock 규칙**: 상대 무응답 10분(기본 600s) 또는 공통 후보 0개 → cha에게
   에스컬레이션 DM("인간 협의 필요") 후 종료. 캘린더 쓰기 0건. 거절 시 재협상은
   정확히 1회.
5. **정확한 단일 시각은 조율하지 않는다(calendar와 상호 가드)**: `request`는 피어에게
   질의를 보내기 전에 해속된 범위가 `--duration-min`과 같은 **정확한 단일 슬롯**인지
   검사한다(`_reject_calendar_intent`). 조율 의사(`조율`/`가능한 시간`/`물어봐` 등)
   없이 단일 슬롯이면 exit 2 `ROUTING-REJECT`로 calendar 스킬을 안내하고 종료한다 —
   피어 네트워크 I/O 전에 차단(선례 사고 2026-07-20: `오전 10시` 고정 요청이 07-29 09:00
   조율로 표류). 캘린더 쓰기 0건.

## 사용 (CLI = `python3 ~/.hermes/skills/coordination/scripts/coordinate_cli.py …`)

### 1) cha가 "피어와 내일 미팅 잡아줘" 요청 시

날짜를 손으로 계산하거나 ISO 날짜를 작성하지 않는다. `--when`에 cha가 말한
자연어 날짜/시간대(예: `내일 오후`)를 그대로 전달한다. 모호하면 cha에게 되묻기
(예: "내일 몇 시부터 몇 시 사이가 좋으세요? 길이는 30분?"), 실행:

```bash
python3 ~/.hermes/skills/coordination/scripts/coordinate_cli.py request \
  --peer peer-test --summary "피어 미팅" \
  --when "내일 오후" \
  --duration-min 30
```

`--when`은 calendar 스킬의 KST 날짜 해석으로 결정론적으로 범위로 바뀐다
(오전 09:00–12:00, 오후 12:00–18:00, 저녁 18:00–21:00, 종일/미지정
09:00–18:00). `--range-start`와 `--range-end`는 이미 확정된 ISO 범위가 있을 때만
함께 사용하며, `--when`과 동시에 사용하지 않는다.

- 상대 에이전트 가용시간 질의 → 내 캘린더와 교집합 → 후보 ≤3개 → 상대 승인.
- 성공 시 `PENDING-OWNER draft=<draft-id> …` (exit 7)를 출력하고 cha DM으로
  후보 슬롯을 보낸다. **cha는 그 DM에서 ✅를 눌러 확정하거나 ⛔를 눌러 취소한다.**
  `coordination-confirm-watch` no-agent cron이 1분마다 소유자 반응만 독립 확인한다.
  **이 단계까지 캘린더에 아무것도 쓰지 않는다.**
- `DEADLOCK …` (exit 4): 에스컬레이션 DM이 이미 발송됨. cha에게 결과만 전달.
- `REFUSED …` (exit 5): 재협상 1회 후 종료됨. cha에게 결과만 전달.
- `ROUTING-REJECT …` (exit 2): cha가 **정확한 단일 시각**(예: `오전 10시`)을 지정했고
  조율 의사가 없는 요청이다 — 피어 질의 전에 거부된다. 이 요청은 calendar 스킬의
  `draft-create`로 등록한다(피어 이름은 제목 토큰). 조율이 필요하면 `--when`을
  범위(예: `오전`)로 주거나 summary/요청에 `조율` 의사를 담아 다시 실행한다.

### 2) 기본 UX: DM 반응

- ✅ (`\u2705`, `:white_check_mark:`): 확정. watcher가 저장된 draft 해시·DM 메시지와
  cha의 비봇 반응을 다시 바인딩한 뒤 finalize를 실행한다.
- ⛔ (`\u26d4`, `:no_entry:`): 취소. ⛔가 ✅보다 우선하며 `calendar_cli.py discard --draft`
  로 초안을 폐기한다.
- 24시간이 지나면 watcher가 초안을 폐기하고 cha에게 만료를 DM으로 알린다.

### 3) 텍스트 fallback: cha가 `실행 <draft-id>` 라고 DM 답장한 뒤에만

```bash
python3 ~/.hermes/skills/coordination/scripts/coordinate_cli.py finalize \
  --draft <draft-id> --slot <승인된 ISO 슬롯> --summary "피어 미팅" \
  --duration-min 30 --correlation <coord-id>
```

finalize는 calendar 스킬 confirm(소유자 DM 재검증, fail-closed)을 거쳐 실행하고,
#team에 간결 확정 통지 1건(조율 봉투는 #autophagy-agents) + cha DM 결과를 보낸다. cha가 `취소 <draft-id>` 라고
하면 `calendar_cli.py discard --draft <draft-id>`로 폐기하고 종료한다.

### 4) watcher 설치

배포 파이프라인은 `skills/coordination/deploy.sh`에서 기존 cron이 없을 때만 다음을
설치한다. 이름은 `coordination-confirm-watch`, 간격은 1분, no-agent/local delivery다.

```bash
hermes cron create "*/1 * * * *" --name coordination-confirm-watch \
  --no-agent --script confirm_reaction_watch.py --deliver local
```

## Sandbox scenario

`scripts/scenario.sh`: 오프라인으로 순수 상태머신 불변식(양측 승인 전 쓰기 명령
0건, deadlock/거절 종결, 재협상 1회 상한, 후보 ≤3)과 토큰 부재 시 fail-closed를
검증하고 `SCENARIO-PASS`를 출력한다. 네트워크 호출·실시크릿 없음.
