# mail triage classification prompt v1 (W4-2)

- Consumer: `skills/mail/scripts/triage_llm.py::classify` — the pipeline's
  step ② (the deterministic sensitivity gate already ran in step ①).
- Routing: non-sensitive → LiteLLM `glm-main`; sensitivity-gate hit → the
  non-GLM quality tier (openai-codex one-shot). Same prompt for both.
- Contract: the response MUST contain exactly one JSON object with keys
  `category` ("important"|"normal"|"spam"), `reply_needed` (bool),
  `schedule_needed` (bool), `budget` (bool), `schedule_text` (string),
  `reason` (string). Parsed by `triage_core.parse_classification`
  (first balanced JSON object; category enum enforced).
- The `<<<PROMPT>>>` marker line below anchors the template start
  (line-anchored split — do not mention the marker in prose above a use
  that could leak; the loader matches a line that IS the marker).
- 변경 시 이 파일을 직접 고치지 말고 버전 파일명(v2, v3…)을 올려라.

<<<PROMPT>>>
다음은 수신된 기관메일 1건이다.

[메일 제목]
{{SUBJECT}}

[발신자]
{{SENDER}}

[메일 본문]
{{BODY}}

위 메일을 분류하라. 규칙:
- category: "important"(회신/일정/과제비 등 조치 필요), "normal"(정보성/공지), "spam"(광고/스팸) 중 하나.
- reply_needed: 내가(수신자가) 답장을 보내야 하는 메일이면 true.
- schedule_needed: 회의/미팅/세미나 등 일정 등록이 필요한 메일이면 true.
- budget: 과제비/예산/정산/구매 관련이면 true.
- schedule_text: schedule_needed가 true일 때, 일정을 한 문장의 한국어로 요약 (예: "7월 20일 오후 3시 연구 미팅"). 날짜와 시각이 본문에 명시된 경우에만 그대로 옮겨 적고, 불명확하면 빈 문자열 "".
- reason: 판단 근거 한 문장.

다른 설명 없이 아래 형태의 JSON 객체 하나만 출력하라:
{"category": "...", "reply_needed": false, "schedule_needed": false, "budget": false, "schedule_text": "", "reason": "..."}
