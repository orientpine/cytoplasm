---
name: mail
description: "기관메일(mailon.kr) 스킬. 메일 작성·발송 지시에서 수신자가 이메일 주소가 아니라 사람 이름이면(예: '홍길동 박사님께 메일') 반드시 먼저 resolve로 이름→이메일 해석(기관 웹메일 자동완성 기반, READ-ONLY) — 메일함 검색만으로 포기하거나 주소를 추측·유추하는 것은 금지. 읽기: list/get/classify/status/resolve 래퍼(W4-1, READ-ONLY). 파이프라인(W4-2): 수신메일 민감도 게이트→분류(glm-main, 민감건은 비-GLM)→다이제스트(08:00 KST)→소유자 지시 기반 회신 초안(비-GLM)→행위 봇의 소유자 DM 승인 게이트→mailon 발송→approvals.jsonl. 새 메일 작성(compose)도 동일 watch·해시 바인딩의 owner DM 확정 게이트를 경유한다. 발송은 반드시 승인 게이트 경유 — 직접 send 금지. 민감 회신은 승인 DM 한 메시지에 전문과 sha256을 함께 표시한다. 승인 표면은 `approval_surface.py` 정책과 draft의 저장된 바인딩으로 결정된다."
version: 1.5.8
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Mail, Institutional, Triage, Approval-Gated, Autophagy]
prerequisites:
  commands: [python3]
---

# 기관메일 스킬 (mail) — W4-1 읽기 래퍼 + W4-2 triage 파이프라인

`~agent/emailAutomation`의 mailon CLI를 감싸는 스킬. mailon의 명령 형태·stdout
계약·종료코드는 전부 `scripts/mail_wrapper.py` 한 파일에 상수로 캐싱되어 있다
(단일 인터페이스 파일). W4-2 파이프라인은 그 위에 승인 게이트 발송을 얹는다.

## 읽기 명령 (W4-1, READ-ONLY)

```bash
# 최근 5건 (라이브 수집 후 state.db 읽기; 민감 표면용은 --masked)
python3 ~/.hermes/skills/mail/scripts/mail_wrapper.py list --limit 5 --sync --masked

# 로컬 읽기만 (브라우저/네트워크 0)
python3 ~/.hermes/skills/mail/scripts/mail_wrapper.py list --limit 5

# 단건 조회 (--body는 agent 홈 안에서만; 마스킹 시 본문 대신 sha256+bytes)
python3 ~/.hermes/skills/mail/scripts/mail_wrapper.py get <uid> --body

# 메타데이터 분류 (제목/발신자 문자열만 사용, LLM 무경유)
python3 ~/.hermes/skills/mail/scripts/mail_wrapper.py classify --uid <uid>

# 수신자 이름→이메일 해석 (기관 웹메일 자동완성 기반, READ-ONLY; 민감 표면용은 --masked)
python3 ~/.hermes/skills/mail/scripts/mail_wrapper.py resolve --name "<이름>"

# mailon 상태를 JSON으로
python3 ~/.hermes/skills/mail/scripts/mail_wrapper.py status
```

stdout은 항상 **정확히 하나의 JSON 객체**다 (`"wrapper": "mail-wrapper-v1"`).

### 수신자 이름→이메일 해석 (resolve)

- compose/발송 지시에서 수신자가 이메일 주소가 아니라 **사람 이름**이면, 주소를 추측하지 말고 반드시 `resolve --name`으로 먼저 해석한다.
- 출력은 wrapper JSON 1객체: `candidates[]` 항목 = `{group, name, email, org}`, group은 `organization`(조직도)/`contacts`(개인 주소록)/`history`(수발신 이력)/`unknown`.
- 후보 판정 규칙 (fail-closed): **0건 → 주소를 지어내지 않고 cha에게 해석 실패를 보고**한다. **정확히 1건 → 그 주소를 사용**한다. **2건 이상 → organization 그룹을 먼저 제시하는 순서로 후보 목록을 cha에게 보여주고 선택을 기다린다** (자동 선택 금지).
- 최종 안전장치는 기존 compose owner-✅ 게이트다 — 승인 DM에 실제 주소가 그대로 표시되므로 소유자가 최종 확인한다.
- resolve는 읽기 전용이다: 브라우저 자동완성 조회만 수행하며 발송 트리거를 절대 호출하지 않는다.

### 발송 모드 상태 보고 규칙 (추측 금지)

cha에게 메일 상태를 보고할 때, **발송 모드(full-go / read-go / no-go)는 절대 추측하지 말고 반드시 아래 권위 명령의 `effective=` 값으로만 보고**한다:

```bash
python3 ~/.hermes/skills/mail/scripts/triage_cli.py mode
```

출력 예: `MODE effective=full-go runtime=… repo=… consecutive_send_failures=0`.
- `effective=full-go` → 발송 가능.
- `effective=no-go` → 차단(승인 실발송 연속 2회 실패 또는 수동 강등; `mode-switch.jsonl`에 사유). 복구는 사람/오케스트레이터가 런타임 파일 삭제·재작성.
- `effective=read-go` → 읽기만.

mode 머신러리 설명("2회 실패→no-go", "둘 다 없으면 no-go 폴백")만 보고 상태를 단정하지 말 것 — 반드시 위 명령을 실행해 실제 `effective=`를 확인하고 그 값을 그대로 보고한다.


### 종료코드 (mailon → 래퍼 매핑)

| mailon | 의미 | 래퍼 동작 | 래퍼 exit |
|---:|---|---|---:|
| 0 | 성공 | 정상 payload | 0 |
| 1 | 설정 오류(env 누락) | `config_error` + 안내 | 1 |
| 2 | 로그인/브라우저 오류 | `auth_error` + **재인증 안내** | 2 |
| 3 | sync 구조 실패 | state.db 로컬 폴백(가능 시 exit 0 + 폴백 표기, 불가 시 3) | 0/3 |
| 10/11 | Windows 런처 사전점검(리눅스 미발생) | `environment_error` | 6 |
| — | uid 없음/DB 비어있음 | `not_found` | 5 |
| — | 서브프로세스 타임아웃 | `timeout` + 잔여 chrome 정리 안내 | 7 |

주의: **20은 종료코드가 아니라 수집 계약의 페이지 크기**(20건/page, 최대
500 page — mailon/scraper.py PAGE_SIZE)다.

## W4-2 triage 파이프라인 (`scripts/triage_cli.py`)

두 개의 루프와 owner DM에서 확정하는 소유자 지시 기반 회신·compose 초안 플로우로 운영된다.

### 1. 승인 및 발송 루프 (watch cron 주기)
Hermes cron(`mail-triage-watch`, no_agent)이 `watch`를 돌린다. 이 루프는 **자동으로 초안을 생성하지 않으며**, 오직 다음 작업만 수행한다:
- 아직 게시되지 않은 대기 중인 초안을 draft 레코드에 바인딩된 채널에 게시한다. 새 회신·compose 초안은 owner DM에 게시한다.
- 게시된 승인 메시지의 ✅(확정) / ⛔(취소) 리액션을 확인하고 처리한다. v1에 저장된 기존 회신은 원래 개인 서버 `#approvals`에서 계속 소비한다.
- 승인된 초안의 실제 메일 발송.

### 2. 다이제스트 루프 (매일 08:00 KST)
신규 cron `mail-daily-digest`(`0 8 * * *`, no_agent)가 `digest`를 실행한다.
- 마지막 다이제스트 이후 수신된 메일을 Discord Markdown 카드 형식(### N. 제목, 상태 배지, 인용문 요약, KST 수신 시각, 코드 스타일 UID/발신 해시)으로 요약하여 cha에게 DM으로 전송한다. 배지는 중요도와 속성에 따라 한글·이모지(🔴 중요/🔵 일반/🗑️ 스팸, ↩️ 회신 필요, 📅 일정, 💳 예산, 🔒 민감, 👀 참조(CC))로 표시된다.
- **DM 전송**: 공유 interop 전송기(DiscordTransport)를 통해 2,000자 이하 순서 보장 청크로 분할 발송된다(429 Retry-After 대응). 다이제스트·발송 결과 DM 등 `dm_owner`를 쓰는 모든 표면에 동일 적용.
- **동기화 폴백**: mailon 동기화(`list --sync`) 실패 시 로컬 `state.db` 기준으로 폴백 발송하며, DM 최상단에 "⚠️ mailon 동기화 실패 — 로컬 DB 기준 (재인증 필요할 수 있음)" 경고를 부착한다. `--no-sync` 명시 실행의 실패는 폴백 없이 fail-closed.
- **실패 처리**: 모든 digest 실패(빌드 단계 LLM 실패, DM 전송 실패)는 기록 없이 단일 행·레닥션된 구조화 마커 `DIGEST-FAIL stage=<build|deliver|runner> retry_safe=<true|false> code=<...>`로 종료하며(주소·본문·토큰 미포함), 다음 tick에 재시도한다(DM-first/record-after 불변식 유지 — cursor는 전달 성공 후에만 기록). 워처는 `retry_safe=true` 마커만 인틱 재시도한다(전달 실패는 일부 청크가 이미 나갔을 수 있어 `retry_safe=false`). cron은 `--deliver discord`로 등록되어, no-agent 스크립트의 **stdout**(성공=빈 stdout=무음, 실패=마커 1행+exit 1)이 소유자 DM으로 전달된다 — `--deliver local`(전달 대상 0개)이었던 2026-07-31에는 실패가 소유자에게 도달하지 못했다.
- **항목 단위 fail-open (분류)**: 한 메일의 분류 LLM 호출이 실패(glm-5.2 timeout 또는 파싱 불가한 비-JSON 응답)해도 다이제스트 전체가 중단되지 않는다. 해당 항목은 보수적으로 `🔴 중요` + `⚠️ 분류 실패` 배지로 표면화되고(플래그는 모두 미부여 — 조작된 판정으로 캘린더 초안을 위임하지 않음), 나머지 메일은 정상 전달·기록된다. 요약 실패가 `(요약 실패)` fallback으로 항목을 유지하는 것과 동일한 취지다. (분류가 파싱은 되나 bool을 문자열로 준 경우 `"false"`가 참으로 새는 버그도 함께 차단 — `_json_bool` 엄격 파싱.)
- 민감 메일은 DM에는 전문이 포함되나, 로컬 DB에는 마스킹된 제목과 빈 요약만 저장된다(제약 7).
- 일정 예약이 필요한 메일은 이 단계에서 캘린더 스킬로 초안 생성이 위임된다.
- **참조(CC) 수신 메일**: cha가 To가 아닌 Cc로만 수신한 메일은 회신 대상이 아니다
  (frontmatter `to:`/`cc:`를 `MAILON_ID`와 대조 — `scripts/triage_recipient.py`).
  파이프라인은 `reply_needed`를 자동 억제하고 flags에 `cc`를 표기하며
  자동 회신 초안을 만들지 않는다(`cc-no-reply`). 판별 불가 시에는 기존 동작 유지(fail-open).
  **대화형 요약/보고에서도** `cc` 플래그 메일은 '회신 필요'가 아닌 '참조(CC)' 항목으로 묶어 제시한다.

### 3. 소유자 지시 기반 초안 생성
cha가 다이제스트를 보고 지시를 내리면(예: "3번 메일, 참석 가능하다고 회신해줘"), 에이전트는 다음 절차를 따른다:
1. `digest-items` 명령으로 번호(N)를 실제 `uid`로 변환한다.
2. `draft --uid <uid> --instruction "<지시문>"` 명령을 실행하여 초안을 생성하고 owner DM에 게시한다.
3. 초안은 `prompts/reply-draft-v2.md`를 사용하며, 항상 비-GLM 티어에서 생성된다.

### 4. 새 메일 작성 (compose — owner DM 확정)
cha가 DM에서 새 메일 발송을 지시하면, 이중 승인 없이 owner DM에서 한 번만 확정한다:
1. `compose --to <주소> [--cc <주소> ...] --subject "<제목>" --body "<본문>" [--attachment <경로> ...]` — 초안 생성 + owner DM 게시(✅·⛔ 미리 부착). `--cc`는 여러 번 지정할 수 있고 To와 함께 승인 해시에 바인딩된다. 첨부는 최대 10개·개별/전체 25 MiB이며 반복 옵션 순서대로 파일명·크기·MIME·내용 해시가 승인에 바인딩된다. 승인 후 수신자·본문·첨부가 바뀌거나 일부 업로드만 성공하면 최종 발송을 거부한다.
   - **수신자 연속성 규칙 (2026-07-20, 소유자 지시)**: 후속·확정·변경 안내 메일은 **직전 관련 발송 메일의 전체 수신자 집합에서 시작**한다. 수신자를 제외할 때는 compose 요청에 제외 사유를 명시한다. 게이트는 최근 24시간 내 제목 토큰이 겹치는 발송 compose와 비교해 빠진 수신자를 승인 DM에 ⚠️ 경고로 표시한다(발송 차단은 아님 — 최종 판단은 소유자 ✅). 이 결정론적 가드는 **compose 발송분만** 인식한다 — 게이트 밖 발송·회신(reply)은 비교 대상에 없으므로 LLM이 이 규칙을 1차 방어선으로 지켜야 한다.
2. cha가 DM 승인 메시지에 ✅ 리액션 → 같은 `watch` cron이 다음 tick에 감지하여 발송(⛔ = 폐기).
3. 회신 초안과 완전히 동일한 게이트를 재사용한다(동결 argv·sha256 해시 바인딩·소유자 전용·⛔ 우선). 승인 채널만 draft 레코드의 `channel_id`(owner DM)로 바인딩되며, compose 초안은 `channel_id` 없이 개인 서버의 #approvals로 폴백하지 않는다(fail-closed — 전문 유출 방지).

#### 세미나 출장 신청 안내 표준 (cha 실제 발송본 기준)

cha가 세미나/워크샵 **출장 신청 안내** 또는 그에 딸린 정산·배차 안내를 요청하면 **가장 최근의 유사한 cha 보낸메일을 먼저 조회**하고 아래 형식을 기본값으로 사용한다. 일반 증빙 안내에는 이 템플릿을 적용하지 않는다. 2026-07-27 실제 발송본 `재난농업건설 필드AI 워크샵 출장 신청 및 배차 안내`가 대표 표본이다.

1. **제목:** 행사명을 먼저 쓰고 실제 행위를 붙인다. 예: `<행사명> 출장 신청 및 배차 안내`. `식비 N회 차감` 같은 단일 세부사항만 제목으로 삼지 않는다.
2. **도입:** `안녕하세요. <owner-name>입니다.` 한 줄 다음에 `출장 신청을 위한 <행사명> 안내사항을 아래와 같이 전달드립니다.`처럼 목적을 바로 밝힌다.
3. **행정 핵심 목록:** `출장명 → 기간 → 장소 → 세미나 구성 → 식비 차감 → 숙박비 결제·정산 방식 → 출장 계정(과제코드 포함)` 순서로 쓴다.
4. **비용·집결·배차:** 건별로 제공된 경우 `납부액·입금계좌·기한 → 집결 시각·장소 → 차량/운전자/동승자 배차표` 순서로 이어 쓴다. 같은 차량 구성원에게 일정 조율을 요청한다.
5. **첨부:** 세부 일정은 Markdown 일정표로 첨부하고 본문 끝에서 확인을 요청한다. 최종 발송본 파일명에는 `초안`을 남기지 않는다. 출장 신청 화면·예시가 있으면 `추신: 아래와 같이 출장 신청하시면 됩니다.` 뒤에 제공한다.
6. **종결:** `감사합니다.` 다음 `<owner-name> 올림`으로 끝낸다.
7. **값 재검증:** 계좌번호·금액·납부기한·과제코드·수신자·배차·식비 차감 횟수는 이전 행사에서 재사용하지 말고, 이번 요청·보낸메일·첨부의 최신값으로 검증한다. 값이 없으면 추측하지 않는다.

### 5. 수동 Triage (Legacy)
`process` 명령은 수동으로 전체 파이프라인(수집→분류→초안 생성→게시)을 실행할 때 사용하며, cron에서는 더 이상 호출되지 않는다.
### 6. 승인 게이트 규칙
- **owner DM 승인 게이트**: 민감 회신은 완성된 회신 전문과 sha256을 하나의 승인 DM에 함께 표시한다. 원본 수신메일의 제목·본문은 승인 메시지에 넣지 않는다.
- **이모지 리액션**: 봇이 owner DM 승인 메시지에 ✅(확정)와 ⛔(취소)를 미리 추가한다.
- **소유자 전용**: cha 본인의 리액션만 인정하며, 봇이나 타인의 리액션은 무시한다.
- **⛔ 우선**: ✅와 ⛔가 함께 있으면 취소로 처리한다.
- **Hash 바인딩**: 모든 승인은 초안의 해시와 바인딩되어 fail-closed로 작동한다.
- **텍스트 fallback**: `실행/취소 <id>` 텍스트는 대체 수단일 뿐이며, 에이전트는 소유자에게 텍스트 확인 지시를 보내지 않는다.
- **승인 채널 바인딩**: 승인 채널은 draft 레코드의 `channel_id`·`surface`·`policy_version`으로 결정된다 — 새 회신·compose 초안은 owner DM이고, v1에 저장된 회신은 개인 서버 `#approvals`에 남는다. 병렬 confirm 경로 없이 같은 watch cron·리액션 규칙·해시 바인딩을 공유한다.
- **키당 라이브 승인 메시지 1건**: 게시는 공유 승인 생명주기(`triage_approval` → `automation/interop/approval_lifecycle.py`)를 거친다. 승인 키는 `mail:{kind}:{uid}`이며 (a) 같은 초안·같은 해시의 재요청은 아무것도 게시하지 않고 기존 메시지를 재사용, (b) 내용이 바뀌면 옛 메시지를 **먼저 삭제한 뒤** 그 레코드를 unbind하고 새로 1건만 게시, (c) 승인 메시지가 사라졌으면 재게시, (d) cha가 이미 ✅/⛔ 한 요청은 파괴하지 않고 워처가 소비하도록 연기한다. 초안 레코드의 `message_id`(= Discord 승인 메시지 id)는 오직 이 게이트의 commit만 기록한다. 초안을 읽지 못하면 "미결 없음"이 아니라 **거부**다(fail-closed).
  - **수정 초안 교체 검증**: `discard --draft`는 초안 상태만 폐기하고 기존 Discord 승인 메시지를 남길 수 있다. 수정 요청에서는 구 초안의 `message_id`를 조회해 실제 메시지 삭제를 확인한 뒤 새 초안을 게시하고, 동일 UID에 라이브 승인 메시지가 정확히 1건인지 재조회한다.
- **producer/watcher lease**: 같은 키에 대한 에이전트 턴과 2분 `watch` cron tick은 `~/.hermes/mail-triage/approval-leases/`의 flock으로 직렬화된다(cron 래퍼에 단일 인스턴스 flock이 없어 실제로 겹친다). 레이스에서 진 쪽은 아무것도 바꾸지 않고 `deferred:lease-held`로 물러난다.


SQLite claim-before-draft로 중복 초안/발송을 방지하며, 승인된 실발송 연속 2회 실패 시 mail-mode를 no-go로 강등한다.

```bash
# 다이제스트 (08:00 cron; 동기화 실패 시 로컬 DB 폴백)
python3 ~/.hermes/skills/mail/scripts/triage_cli.py digest

# 다이제스트 아이템 확인 (N -> uid 변환용)
python3 ~/.hermes/skills/mail/scripts/triage_cli.py digest-items

# 소유자 지시 기반 초안 생성
python3 ~/.hermes/skills/mail/scripts/triage_cli.py draft --uid <uid> --instruction "참석 가능하다고 회신해줘"

# 새 메일 작성 (owner DM 확정 게이트)
python3 ~/.hermes/skills/mail/scripts/triage_cli.py compose --to <주소> --cc <참조주소> --subject "<제목>" --body "<본문>"

# 첨부 포함 새 메일 (여러 파일은 --attachment 반복)
python3 ~/.hermes/skills/mail/scripts/triage_cli.py compose --to <주소> --subject "<제목>" --body "<본문>" --attachment /private/report.pdf

# 프로덕션 watch (10분 cron)
python3 ~/.hermes/skills/mail/scripts/triage_cli.py watch

# 수동 triage (Legacy)
python3 ~/.hermes/skills/mail/scripts/triage_cli.py process --limit 10

# 승인 거부(초안 폐기) / 목록 / 모드 확인
python3 ~/.hermes/skills/mail/scripts/triage_cli.py discard --draft <id>
python3 ~/.hermes/skills/mail/scripts/triage_cli.py list-drafts
python3 ~/.hermes/skills/mail/scripts/triage_cli.py mode
```

## 절대 규칙

1. **발송은 게이트 경유만**: mailon send를 직접 호출하지 않는다. 승인 없는
   발송 경로는 존재하지 않으며, 직접 터미널 호출은 배포된 외부효과 게이트
   (`mailon_send` 룰)가 fail-closed로 차단한다.
2. **민감 라우팅 (제약 6)**: 민감도 게이트 적중 메일의 본문·초안은 GLM에
   절대 넣지 않는다. 게이트가 LLM보다 먼저 실행된다.
3. **마스킹 (제약 7)**: 실제 제목·발신자·본문을 QA/리포트/공개 채널/repo/git에
   절대 쓰지 않는다. 민감 회신의 완성 본문은 owner DM 승인 메시지에만 표시하며, 원본 수신메일과
   민감 초안은 `~agent/mail/`(700) 밖으로 내보내지 않는다.
4. **읽기 전용 래퍼 유지**: `mail_wrapper.py`의 mailon 호출 표면은 sync/status/resolve
   뿐이며 그 외 mailon 명령은 코드로 거부한다(send는 triage 게이트의 동결 argv 전용).
   래퍼 공개 서브커맨드는 list/get/classify/status/resolve다.
5. 인증 실패(exit 2) 시 자동 무한 재시도 금지 — 재인증 안내를 따르고 반복
   실패는 cha에게 보고. 연속 발송 실패 2회는 mail-mode 재판정(no-go)이다.

## Gmail 발송 운영 (RTS-3)

### 계정 선택

- 새 Gmail 메일은 요청에서 `account=gmail`을 명시해야 한다. 계정이 없으면 임의 기본값을
  고르지 않고 거부한다.
- 답장은 원본 스레드 계정을 상속한다. Gmail 스레드는 Gmail으로, KIMM 스레드는 KIMM으로
  유지한다. 소유자가 명시한 계정만 이 상속보다 우선한다.
- KIMM은 기존 MailOn argv를 그대로 사용한다. Gmail 전용 `gws` 경로와 혼용하지 않는다.

### 소유자 DM 승인 형식

Gmail 초안은 기존 mail 승인 라이프사이클을 재사용하여 소유자 DM에 한 건만 게시한다. 메시지에는
발신 계정, 작업(`+send` 또는 `+reply`), 수신자, 회신 대상, 제목, 본문, draft SHA-256 및 action
hash가 들어간다. 첨부마다 파일명·바이트 크기·SHA-256을 순서대로 표시하며 로컬 원본 경로는
표시하지 않는다. cha의 ✅만 실행을 허용하고 ⛔는 우선 취소한다.

운영 발송 승인은 DM의 ✅/⛔ 리액션만 사용한다. 서명된 텍스트 승인은 오프라인 E2E에서만 허용되는
시험 경로이며 운영 발송 승인으로 사용하지 않는다.

### 감사 로그 조회

`TRIAGE_APPROVAL_LOG`가 설정되어 있으면 그 경로를, 없으면
`/srv/autophagy-agents/logs/approvals.jsonl`을 읽는다. 다음 조회는 본문·수신자·경로를 출력하지
않고 Gmail 승인/실행의 action hash와 결과만 표시한다.

```bash
python3 - "${TRIAGE_APPROVAL_LOG:-/srv/autophagy-agents/logs/approvals.jsonl}" <<'PY'
import json
import sys
from pathlib import Path

for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    record = json.loads(line)
    if record.get("action") in {"external_effect.approval", "gmail.approval_execution"}:
        print(json.dumps({key: record.get(key) for key in ("action", "hash", "result")}, ensure_ascii=False))
PY
```

### Fail-closed 오류와 재승인

- 승인 없음·거부·만료(15분)·중복 승인 레코드는 발송하지 않는다.
- 본문·수신자·회신 대상·동결 argv가 달라지거나 첨부가 교체·수정·삭제되면 발송 직전 중단한다.
- 성공 기록이 있는 동일 action hash는 재시도해도 두 번째 메일을 보내지 않는다.

이 오류가 나오면 `approvals.jsonl`이나 기존 초안을 손으로 고치지 않는다. 원래 요청에서 새 Gmail
초안을 만들고, 변경된 DM 내용을 cha가 다시 확인한 뒤 새 메시지에 ✅를 남긴다. 인증·서비스 오류는
먼저 재인증 또는 서비스 복구를 확인하고 같은 절차로 새 승인을 받는다.
