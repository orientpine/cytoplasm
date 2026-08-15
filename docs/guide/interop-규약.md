# Autophagy Lab: 인터롭 규약 v0 (Interop Protocol v0)

이 문서는 `autophagy-agents` 프로젝트의 에이전트 간 상호운용성을 위한 기초 규약(v0)을 정의합니다. 본 규약은 업무 보고, 봇간 질의/응답, 그리고 서버 안전 규칙을 포괄하며, 프로젝트의 모든 에이전트와 관련 서비스(콜렉터, 온보딩 킷 등)가 준수해야 하는 **단일 진실 공급원(Single Source of Truth)**입니다.

- **대상**: Hermes 에이전트, OpenClaw 폴백 에이전트, 제3자 연구원 에이전트
- **참조**: 계획 작업 W1-6(구현), W3-4(콜렉터), W3-5(온보딩), 크로스커팅 제약 9(준수)

---

## 1. 업무 보고 규약 (`#agents-log`)

모든 에이전트는 자신의 주요 작업 상태 변화를 `#agents-log` 채널에 규격화된 포맷으로 보고해야 합니다. 보고는 각 설치의 개인 RAG 인제스트가 소비하며, 그룹 관리자가 선택한 경우 선택적 `Autophagy-Hub` 콜렉터(W3-4)가 대시보드에도 시각화합니다.

### 1.1. 메시지 포맷
메시지는 반드시 **JSON 코드 블록** 형태여야 하며, 파서가 정규표현식으로 추출할 수 있도록 독립된 블록으로 작성되어야 합니다.

- **정규표현식 가이드**: `^```json\n\{[\s\S]*\}\n```$`
- **필드 정의**:
  - `version`: (string) 규약 버전. 현재 `"v0"`.
  - `agent_id`: (string) 에이전트 식별자 (예: `"agent-cha"`, `"peer-test"`).
  - `task_id`: (string) 작업 고유 ID (UUID 또는 타임스탬프 기반).
  - `status`: (enum) 작업 상태. `start` (시작), `done` (완료), `blocked` (차단/지연).
  - `summary`: (string) 한 줄 요약 (한국어 권장). **공유 채널 노출 제약은 §1.3 준수(MUST) — 내부 상세·민감 본문 금지.**
  - `links`: (array of strings) 관련 링크 (Discord 메시지 링크, 문서 링크 등).
  - `timestamp`: (string) RFC3339 포맷 (KST 기준 권장, 예: `2026-07-15T14:30:00+09:00`).

### 1.2. 예시

#### ✅ 준수 예시 (Conformant)
```json
{
  "version": "v0",
  "agent_id": "agent-cha",
  "task_id": "task-12345",
  "status": "done",
  "summary": "W1-6 인터롭 규약 문서 작성 완료",
  "links": ["https://github.com/orientpine/autophagy/blob/main/docs/guide/interop-규약.md"],
  "timestamp": "2026-07-15T15:00:00+09:00"
}
```

#### ❌ 비준수 예시 (Non-conformant - 파서에 의해 무시됨)
```
에이전트 보고합니다. 작업 끝났어요!
상태: 완료
ID: 12345
```
*(이유: JSON 코드 블록 형식이 아니며 필수 필드가 누락됨)*

### 1.2.1. 보고 발신자 신원 바인딩 (MUST)

개인 RAG 인제스트는 Discord 메시지의 실제 작성자 ID를 런타임 roster에서 찾은 뒤,
그 principal과 보고 본문의 `agent_id`를 exact-match한다. 관리자는
`admin.publisher_principal`, `active` 멤버는 `members[].node_label`이 보고 principal이다.
미등록·`removed` 작성자, 불일치 `agent_id`, 누락·불량 roster는 fail-closed로 거부하며
해당 본문은 `LogicalDocument`·내구 큐·MCP `load_memory` 어느 단계에도 들어가지 않는다.
거부는 본문을 기록하지 않고 메시지 ID·마스킹된 작성자·claimed ID만 warning으로 남긴다.

선택적 report hub는 기존 private peer registry 분류를 계속 독립 수행한다. hub의 배포나
`unregistered` 표시는 필수 RAG 경계의 신원 검증을 대체하지 않는다.

---

### 1.3. `summary` 상세도 및 마스킹 가이드라인 (MUST)

`summary`는 자유 텍스트 필드이지만, `#agents-log`는 **공유 Lab 서버의 공용 채널**이며 향후 타 연구자의 개인 봇도 이곳에 보고한다. 따라서 `summary`에는 **작업이 진행 중이라는 활동 신호만** 담고, 작업의 민감한 내부 상세는 절대 노출하지 않는다. 민감 본문·승인 대상은 규약 §3.4에 따라 개인 서버 `#approvals` 또는 소유자 DM으로만 전달된다.

#### 금지 (MUST NOT — 공유 채널 노출 금지)
- **내부 파일 경로·소스 식별자**: 파일명(`calendar_cli.py` 등), 함수/심볼명, 브랜치명, 커밋 해시 원문.
- **본문 발췌·인용**: 메일·문서·특허·예산의 실제 내용이나 인용구.
- **PII·시크릿 모양 문자열**: 실명, 이메일 주소, 토큰/키 모양 문자열(secret-scan 오탐 유발).
- **정량 민감치**: 예산 금액, 비용, 내부 수치.

#### 권장 (SHOULD — 활동 수준으로 추상화)
- **무엇을**은 도메인 수준으로만: 「calendar 스킬 버그 수정」 (O) vs 「calendar_cli.py의 remove_completed 수정」 (X).
- **얼마나**는 규모 수준으로만: 「소스 2건·테스트 1건 변경, 전체 통과」 (O) vs 파일명 나열 (X).
- **다음 무엇이 필요한가**만 명시: 「사람 리뷰 대기」 (O).
- 상세가 필요하면 원문 대신 **접근 통제된 링크**를 `links`에 넣는다(개인 위키·PR 등). `#agents-log`에는 링크만, 본문은 링크 너머에.

#### 예시
- ❌ `blocked: review-required: Code fix for remove_completed bug — 2 source files changed (calendar_cli.py, confirm_reaction_watch.py) + 1 new test file. All 44 tests pass.`
- ✅ `blocked: calendar 스킬 버그 수정 — 소스 2건·테스트 1건 변경, 전체 통과, 사람 리뷰 대기`

작성 측 에이전트는 보고 생성 시 이 가이드라인을 준수해야 하며(MUST), 이 중 결정론적 마스킹은 **코드로 강제된다**: 단일 write 초크포인트인 `automation/interop/report.py`의 `format_report()`가 직렬화 직전 `mask_summary()`를 강제 적용하므로, 모든 전송 경로(hermes_hook·hermes_plugin·gate_driver)가 자동 커버된다. 마스킹 대상: 파일 경로·소스 식별자(`[MASKED_PATH]`), secret/token(`[MASKED_KEY]`·`[MASKED_TOKEN]`·`[MASKED_AUTH]`), 이메일(`[MASKED_EMAIL]`), snowflake 모양 id(`[MASKED_ID]`). 활동 수준 문장은 그대로 통과한다. 콜렉터(`Autophagy-Hub`)는 여전히 형식만 검증하며 마스킹은 작성 측 초크포인트에서 끝난다 — 가드가 없는 경로로 작성된 과거 리포트만 원문이 남을 수 있다(재수집 시 upsert로 갱신 가능). 회귀 고정: `tests/unit/test_interop_protocol.py`.

---

## 2. 봇간 구조화 질의/응답 규약

에이전트 간의 직접적인 협업(위임, 일정 조율 등) 시 사용하는 메시지 봉투(Envelope) 규약입니다.

### 2.1. 메시지 구조
- `version`: (string) `"v0"`.
- `correlation_id`: (string) 요청-응답 매칭을 위한 고유 ID.
- `sender_id`: (string) 발신 에이전트 ID.
- `recipient_id`: (string) 수신 에이전트 ID.
- `intent`: (string) 의도 (예: `query_availability`, `delegate_task`).
- `payload`: (object) 의도에 따른 데이터 스키마.

수신 플러그인은 자기 `recipient_id`인 봉투를 처리하기 전에 Discord 실제 작성자 ID를
roster principal로 해석하고 `sender_id`와 exact-match한다. 관리자 principal은
`admin.publisher_principal`, `active` 멤버 principal은 `members[].node_label`이다. 미등록·
`removed` 작성자, principal 불일치, 누락·불량 roster는 응답 생성이나 결과 전달 전에
`interop_sender_identity_rejected`로 거부하고 warning을 남긴다. 두 소비자는 roster를 각자
읽어 독립 판정하며 공유 trust cache를 두지 않는다.

### 2.2. 가용시간 질의 예시 (W3-3)

**요청 (Query):**
```json
{
  "version": "v0",
  "correlation_id": "corr-999",
  "sender_id": "agent-cha",
  "recipient_id": "peer-test",
  "intent": "query_availability",
  "payload": {
    "range_start": "2026-07-16T09:00:00+09:00",
    "range_end": "2026-07-16T18:00:00+09:00",
    "duration_min": 30
  }
}
```

**응답 (Response):**
```json
{
  "version": "v0",
  "correlation_id": "corr-999",
  "sender_id": "peer-test",
  "recipient_id": "agent-cha",
  "intent": "response_availability",
  "payload": {
    "slots": [
      "2026-07-16T10:00:00+09:00",
      "2026-07-16T14:30:00+09:00"
    ]
  }
}
```

### 2.3. 일정 조율 프로토콜 (Coordination Flow, W3-3)

소유자의 "OO님과 미팅 잡아줘" 요청을 처리하는 표준 흐름입니다. 구현:
`automation/interop/coordination.py`(순수 상태머신) + coordination 스킬(드라이버).

1. **가용성 질의**: 요청 측이 `query_availability`(§2.2 payload: `range_start`,
   `range_end`, `duration_min`, 전부 시간대 오프셋 포함 ISO)를 보낸다.
   `correlation_id`는 반드시 `coord-` 접두사를 쓴다. **`coord-` 봉투(질의/응답)는
   반드시 전용 인터롭 채널인 #autophagy-agents에서 교환되어야 한다(MUST).**
   응답 측 플러그인은 `interop_channel_id` 설정값(미설정 시 소스 채널 폴백)에 따라
   응답을 전송한다.
2. **교집합 → 후보 ≤3개**: 응답 `slots`와 요청 측 캘린더(읽기 전용 list)의
   교집합을 계산해 정렬·중복제거 후 최대 3개 후보를 선택한다.
3. **상대 측 승인**: `query_confirm_slot`(payload: `slot`, `duration_min` — 일정
   제목 등 캘린더 내용은 넣지 않는다) → `response_confirm_slot`
   (payload `result`: `accepted`|`declined`). 공개 v1에서는 상대 소유자의 실제
   승인 경로가 아직 없으므로 이 질의의 응답은 항상 `result: "declined"`이다.
   즉 v1 coordination은 **질의는 되지만 자동 합의는 없다**. 상대 소유자 승인 후
   `accepted`를 보내는 실제 캘린더 기반 경로는 W-F2.5-D(v2)에서 구현한다.
4. **요청 측 소유자 승인 = 캘린더 쓰기 게이트**: 양측 승인 전 캘린더 쓰기
   금지. 요청 측은 W3-1 calendar 스킬 초안(`실행 <draft-id>` DM 확인,
   fail-closed)으로만 자기 캘린더에 등록한다. `실행 <draft-id>` 텍스트 확인은 **대화형 에이전트(게이트웨이)가 처리하는 fallback**이며, cron 워처는 리액션(✅/⛔)만 폴링한다. 확정 후 **#team에는 간결한 확정 통지 1건만 게시하며(액션 아이템·지시·멘션 금지 — 봇 캐스케이드 예방), 조율 과정의 원문 JSON은 #team에 노출하지 않는다.** 상세 결과는 소유자 DM으로 보낸다.
5. **Deadlock 규칙 (MUST)**: 상대 무응답 **10분**(운영 기본값,
   `PRODUCTION_TIMEOUT_S=600`; 테스트는 짧은 값 주입 가능) **또는 공통 후보
   0개** → 소유자에게 에스컬레이션 DM("인간 협의 필요") 후 종료.
   **캘린더 쓰기 0건.**
6. **거절 규칙 (MUST)**: 한쪽이 거절하면 재협상(다음 후보 제안)은 정확히
   **1회**만 허용하고, 재차 거절/후보 소진 시 종료한다. 캘린더 쓰기 0건.

---

## 3. 서버 운영 및 안전 규칙 (Normative Rules)

모든 에이전트 구현체는 다음 규칙을 반드시(MUST) 준수해야 합니다.

### 3.1. 루프 가드 (Loop-guard)
봇 간의 무한 연쇄 응답을 방지하기 위한 보호 장치입니다.
- **속도 제한**: 동일 스레드 내에서 봇 간의 연쇄 응답은 **분당 5회**를 초과할 수 없습니다.
- **중복 억제**: 동일한 내용(메시지 본문 해시 기준)의 메시지가 연속해서 발생할 경우 즉시 차단해야 합니다.

### 3.2. 킬스위치 (Kill-switch)
비상 상황 시 에이전트의 활동을 즉시 중단시키는 명령입니다.
- **명령어**: `!pause-agents` (중단), `!resume-agents` (재개).
- **권한**: 서버 소유자(cha)만 실행 가능해야 합니다.
- **영속성**: 중단 상태는 파일 또는 DB에 영속적으로 저장되어, 에이전트 재시작 후에도 유지되어야 합니다.

### 3.3. Discord 전송 제약
- **메시지 길이**: Discord의 단일 메시지 제한은 **2,000자**입니다. 이를 초과하는 긴 보고서나 데이터는 순서가 보장된 **순차적 청킹(Ordered Chunking)** 방식으로 분할 전송해야 합니다.
- **첨부 파일**: 단일 파일 크기 제한은 **25MiB**입니다. 이를 초과하는 경우 Google Drive 링크 등으로 대체해야 합니다.
- **속도 제한(Rate Limit)**: Discord API의 429 응답 수신 시, `Retry-After` 헤더를 준수하여 백오프(Backoff)를 수행해야 합니다.

### 3.4. 승인 격리 (Approval Isolation)
- **승인 표면은 두 가지**: **스킬 공급망 승인**(skill-deploy + Peer Attestation, skill-publish, managed 활성화)은 연구자별 **개인 서버**의 `#approvals`에서 수행하여 소유자별로 격리합니다. **소유자 전용 승인**(메일·과제비·특허 반출·수리·Drive 아카이빙 등)은 **행위 봇과 소유자의 DM**입니다. 표면은 `automation/interop/approval_surface.py`가 단일 결정하고 conformance 테스트가 강제합니다. 이관 진행 중(AS) — 순서는 `.omo/plans/approval-surface-ssot.md`.
- **Peer는 자기 두 번째 봇**: 배포 attestation의 peer는 타 연구자가 아닌 자기 노드의 peer 봇이므로, 자기 agent와 peer가 함께 있는 개인 서버 `#approvals`만으로 2자 통제가 충족됩니다. 공유 Lab에는 `#approvals`가 없습니다.
- **채널 식별**: 해석은 `automation/interop/approval_directory.py` **한 곳**에서만 이뤄집니다 — 승인 producer가 스스로 채널을 해석하면 conformance 테스트가 빌드를 깨뜨립니다. 디렉터리는 config 키 → 캐시 → guild-scan 순으로 해석하고(다중 매칭 시 fail-closed), 해석된 채널은 **실제 사실을 확인한 뒤**(DM이면 type=1·소유자 수신, 공급망이면 type=0·이름 approvals) 레코드에 영속됩니다. 이후의 모든 읽기·리액션·삭제는 그 저장값을 씁니다. 흐름별 env override(`BUDGET_APPROVALS_CHANNEL_ID` 등)는 호환용으로만 남아 있으며 AS-3.2에서 제거됩니다.
---

## 4. 테스트 및 준수 (Compliance)

### 4.1. 테스트 주입 어댑터 (Injection Adapter)
W1-6 구현 시 포함되는 테스트 어댑터는 다음 계약을 따릅니다.
- `E2E_TEST_MODE=1` 환경 변수가 활성화된 경우에만 작동합니다.
- 서명된 인바운드 이벤트(테스트용 승인 레코드 등)를 실제 사용자의 입력으로 간주하여 처리합니다.
- **보안**: 프로덕션 환경(실제 서비스 유닛)은 반드시 이 환경 변수를 거부하고 기동되지 않아야 합니다.

### 4.2. 버전 관리
- 본 규약은 `v0`으로 시작하며, 하위 호환성이 깨지는 변경 시 버전을 상향합니다.
- 에이전트는 메시지의 `version` 필드를 확인하여 자신이 처리할 수 없는 버전의 메시지는 무시하거나 에러 로그를 남겨야 합니다.

---

**최종 수정일**: 2026-07-21
**작성자**: Sisyphus-Junior (Autophagy Project Orchestrator)

### v1 안전 최소선 (W-F2.5-C)

v1에서 지원하지 않는 `query_*` 의도도 자동 수락하지 않는다. 명시된 allowlist 밖의
질의는 `result: "declined"`, `reason: "unsupported_intent"`로 거부한다.
