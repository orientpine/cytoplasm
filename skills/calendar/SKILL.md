---
name: calendar
description: "cha 본인 Google 캘린더 관리 스킬 (gws CLI). 조회(list)는 게이트 없이 즉시. 생성/수정/삭제는 변경 요약 초안 → cha DM의 ✅/⛔ 반응 확인(텍스트 실행/취소는 fallback) → 실행 + approvals.jsonl 기록 게이트를 거친다. 모호한 시간은 되묻는다. 라우팅: 상대 미지정 요청은 calendar 소유; 피어가 명시돼도 '정확한 단일 시각'이면 제목 토큰으로 보고 본인 단독 일정=calendar; 피어+범위+조율 의사면 coordination으로 ROUTING-REJECT(exit 4); 의도 모호(피어명만/시각+조율 충돌)는 ROUTING-CLARIFY(exit 4, fail-closed)로 되묻는다. W3-1."
version: 1.3.4
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Calendar, GWS, Approval-Gate, Autophagy]
prerequisites:
  commands: [python3]
---

# 내 캘린더 관리 (calendar)

cha 본인의 Google 캘린더를 gws CLI(OAuth, W0-6)로 관리한다.

## 절대 규칙 (안전)

1. **확인 전 변경 금지**: 캘린더 생성/수정/삭제는 오직
   `calendar_cli.py confirm`(소유자 확인 검증 내장)으로만 일어난다.
   `gws calendar events insert/update/patch/delete`를 터미널에서 직접 실행하지
   마라 — pre_tool_call 외부효과 게이트가 승인 없는 호출을 차단한다.
2. **모호한 시간은 추측하지 마라**: CLI가 exit 5 + `AMBIGUOUS-TIME 되묻기:`를
   내면 그 질문을 cha에게 그대로 DM으로 물어보고, 답을 받아 다시 시도한다.
3. **캘린더 내용은 cha의 DM 밖으로 내보내지 마라**: 공개 채널(#team 등)·repo에
   일정 제목/시간을 게시하지 않는다.
4. **피어 관련 요청은 결정론적 라우팅 게이트를 거친다**: `draft-create`는 원문/명시
   제목을 `calendar_routing.classify_meeting_request`로 판정한다(등록 `agent_id`
   검사, 본인 `agent-cha` 제외). **정확한 단일 시각**이 지정되면 피어 이름이 있어도
   제목 토큰으로 보고 본인 단독 일정 초안을 만든다(선례 사고 2026-07-20: `오전 10시`
   요청이 07-29 09:00 조율로 표류). **피어+범위(오전 등)+조율 의사**면 초안 없이
   exit 4 `ROUTING-REJECT … coordination 스킬을 사용하세요`를 낸다. **의도 모호**(피어명만
   있고 시각·조율 신호 없음, 또는 정확한 시각과 조율 의사가 충돌)면 exit 4
   `ROUTING-CLARIFY`로 되묻는다(fail-closed, 초안 없음). 분류 레지스트리
   (`~/.hermes/interop/peers.yaml`, 분류가 필요한 설치에만 생성되는 선택 파일)가
   **없으면** stderr `PEER-REGISTRY-ABSENT …` 한 줄만 남기고 피어 분류 없이 본인 단독
   일정으로 진행하지만, 파일이 **있는데** 읽히지 않거나 깨졌으면 기존대로 실패로 막는다.

## 명령 (CLI = `python3 /srv/autophagy-skills/live/calendar/scripts/calendar_cli.py …`)

### 1) 조회 — 게이트 불요, 즉시 실행

변경 명령은 `/srv/autophagy-skills/live/calendar/scripts/` 밖의 사본에서 실행을 거부하며 `STALE-SKILL-COPY-BLOCK`을 출력한다.

```bash
python3 /srv/autophagy-skills/live/calendar/scripts/calendar_cli.py list [--days 7] [--query 검색어]
```

### 2) 생성 — cha가 DM으로 "내일 오후 3시 실험 미팅 잡아줘" 요청 시

`peer-test와 다음주 오전에 가능한 시간 조율해줘`처럼 피어+범위+조율 의사가 모두 있는 요청에는
이 명령을 쓰지 않는다 — `ROUTING-REJECT`(exit 4)가 나며 `coordination`의
`coordinate_cli.py request --peer peer-test …` 경로로 조율한다. 반면 `peer-test랑 내일
오후 3시 미팅`처럼 정확한 시각이 지정되면 피어 이름은 제목 토큰이므로 이 명령으로 본인
단독 일정을 만든다. 의도가 모호하면 `ROUTING-CLARIFY`로 되물으니 cha에게 그대로 전달한다.

```bash
python3 /srv/autophagy-skills/live/calendar/scripts/calendar_cli.py draft-create \
  --text "내일 오후 3시 실험 미팅"
```

출력의 `CHANGE-SUMMARY`와 `DRAFT-CREATED id=<draft-id>`를 확인한 뒤, 초안에
묶인 소유자 확인 DM을 게시한다.

```bash
python3 /srv/autophagy-skills/live/calendar/scripts/calendar_cli.py post-confirm --draft <draft-id>
```

이 명령은 **요청 하나마다 자기 스레드**(`캘린더 · <draft id>`)를 열고 그 안에 변경
요약과 `sha256`을 게시한 뒤 **✅를 먼저, ⛔를 다음에** 미리 단다. 지시가 승인 채널에서
왔고 `--origin-message-id`를 넘겼다면 그 지시 메시지에 스레드를 앵커한다. 스레드 이름에
들어가는 것은 draft id 뿐이다(절대 규칙 3 — 제목·시각·event id 금지). pending-confirm
JSONL에는 이 확인 메시지의 channel/message id와 초안 SHA-256만, 초안 레코드에는
`approval_thread_id`(승인 해시 밖)만 더해진다. 이 단계도 캘린더에는 아무것도 쓰지 않는다.

### 3) 실행 — cha의 ✅ 반응이 기본, 텍스트는 fallback

cha는 게시된 **같은 확인 DM 메시지에만 ✅로 확정하거나 ⛔로 취소**한다. `calendar-confirm-watch`
no-agent cron이 매분 반응을 읽는다. 소유자 본인(봇 아님)의 반응만 인정하며,
✅와 ⛔가 함께 있으면 **⛔가 항상 우선**한다. 게시 DM SHA-256과 현재 초안 SHA-256이
둘 다 일치하지 않으면 fail-closed로 아무것도 실행하지 않는다. 24시간이 지나면 초안을
폐기하고 cha에게 알린다. `post-confirm`이 불리지 않아 확인 DM 없이 남은 초안(고아)도
같은 워처가 24시간 유예 후 폐기하고 cha에게 알린다 — 초안 생성과 게시가 별개 단계라
게시가 누락되면 어떤 원장에도 남지 않기 때문이다(2026-07~08 실측 33건 누적).

```bash
python3 /srv/autophagy-skills/live/calendar/scripts/calendar_cli.py confirm --draft <draft-id>
```

감시자는 정확한 확인 DM과 draft 해시, ⛔ 우선순위 및 소유자 반응을 한 번 검증한 뒤,
드래프트 ID/해시·DM 채널/메시지·소유자·승인 동작에 묶인 5분짜리 HMAC 서명 일회용
승인 파일을 자식 `confirm`에 전달한다. 자식은 현재 draft/pending과 모든 바인딩을 대조하고
승인 파일을 원자적으로 소비하며, Discord를 다시 조회하지 않는다. 승인 파일 없는 직접
`confirm --draft` 호출은 기존처럼 Discord를 독립 검증하므로 안전하게 호환된다.
cha가 반응을 사용할 수 없으면 `실행 <draft-id>` 또는 `취소 <draft-id>` DM을 fallback으로
보낼 수 있다. 텍스트 `실행`도 Discord REST로 소유자/비봇을 독립 검증하며, pending 반응에
⛔가 있으면 거부된다.
텍스트 취소는 다음과 같다.

```bash
python3 /srv/autophagy-skills/live/calendar/scripts/calendar_cli.py discard --draft <draft-id>
```

### 4) 수정/삭제 — 같은 초안 → 확인 → 실행 게이트

```bash
# 수정: 새 일시(--text)나 새 제목(--summary) 중 바꿀 것만
python3 …/calendar_cli.py draft-update --event-id <id> --text "모레 오전 10시"
# 삭제: list로 event id를 찾은 뒤
python3 …/calendar_cli.py draft-delete --event-id <id> --label "실험 미팅"
```

이후 실행 절차는 생성과 동일하다(초안 → `post-confirm` → ✅/⛔ 또는 텍스트 fallback).

### 5) 결과 통지 — 승인이 이뤄진 그 요청 스레드로

승인 요청·리마인더·결과가 **한 스레드에서 완결된다**. 실행·취소·만료가 확정되면
감시자가 `post-confirm`이 연 그 스레드(`캘린더 · <draft id>`)에 결과를 올리고, 스레드
이름 앞에 상태(`✅ 완료`/`⛔ 취소`/`⌛ 만료`)를 붙여 아카이브한다 — 열려 있는 스레드
목록이 곧 진행 중인 요청 목록이다. 종결 표시가 실패해도 결과 통지는 그대로 남는다
(`THREAD-CLOSE-FAIL` 마커).

cha의 지시가 **채널에서** 왔다면 초안을 만들 때 그 채널/메시지를 함께 넘긴다.
초안 세 명령(`draft-create`/`draft-update`/`draft-delete`) 모두 같은 인자를 받는다.

```bash
python3 …/calendar_cli.py draft-create --text "내일 오후 3시 실험 미팅" \
  --origin-channel-id <채널 id> --origin-message-id <지시 메시지 id>
```

이 인자는 **스레드가 놓이는 자리**만 바꾼다: 지시가 승인 채널에서 온 것이면 요청
스레드가 그 지시 메시지에 걸리고, 아니면 결과 통지가 그 채널의 스레드로 간다. 확인
(✅/⛔)은 종전대로 요청 스레드의 확인 메시지에서만 이뤄지고, 초안 해시에는 들어가지
않는다(같은 변경이면 해시도 같다).

**스레드 문구는 마스킹된다**: 절대 규칙 3에 따라 제목·일시·이벤트 id·캘린더 id는
스레드 이름에도 본문에도 넣지 않는다. 채널에 나가는 것은 동작 종류(등록/수정/삭제),
draft id, 결과, 사유뿐이다(예: `✅ 캘린더 등록 실행 완료 (draft ab12cd) — 소유자 ✅ 승인`).
origin 인자가 없는 초안은 종전 동작 그대로다 — 취소·만료만 cha에게 알리고 성공은 조용하다.
스레드 게시가 실패하면 결과는 cha에게 폴백되고 `NOTIFY-THREAD-FAIL` 마커가 남는다.

## 반응 감시 cron

`deploy.sh`는 배포 시에만 agent의 Hermes에 아래 no-agent cron을 idempotent하게 등록한다.
이 저장소에서는 실행하지 않는다.

```text
calendar-confirm-watch  */1 * * * *  --script confirm_reaction_watch.py --deliver local
```

감시자는 mounted calendar skill의 `calendar_cli.py confirm/discard`만 호출하며, gws를
직접 호출하지 않는다.

## 시간 파싱 규칙

지원: 오늘/내일/모레/글피, N일 뒤, 이번주/다음주+요일, M월 D일, YYYY-MM-DD,
오전/오후 H시[M분|반], HH:MM, 정오/자정, "H시부터 H시까지", "N시간". 기본
길이 1시간. "다음주쯤"·시각 없음·오전/오후 없는 1~12시는 되묻기(exit 5).

## Sandbox scenario

배포 파이프라인용 `scripts/scenario.sh`: 스텁 gws로 초안 무변경, fail-closed
confirm, 모호시간 되묻기, (어댑터 가용 시) 서명 주입 확인 실행 + 승인 기록,
삭제 왕복, 거부 무변경을 검증하고 `SCENARIO-PASS`를 출력한다.
네트워크 호출·실시크릿 없음.
