# mail reply draft prompt v2 (W4-6)

- Consumer: `skills/mail/scripts/triage_llm.py::draft_reply` — the pipeline's
  step ③. ALWAYS the non-GLM quality tier (openai-codex one-shot); the
  Korean final text never goes through GLM regardless of sensitivity.
- Contract: the response MUST contain exactly one JSON object with keys
  `subject` (string; may be empty → caller falls back to "Re: <원제목>")
  and `body` (non-empty string; the final Korean reply text). Parsed by
  `triage_core.parse_reply`.
- The draft is FINAL TEXT: it is sent verbatim after the owner's approval-DM
  ✅ — no post-editing pass exists. Keep it self-contained and safe.
- v2 adds the owner-instruction section: `{{INSTRUCTION}}` is filled by
  `triage_core.build_prompt` ("(별도 지시 없음)" when the owner gave none).
- 변경 시 이 파일을 직접 고치지 말고 버전 파일명(v3, v4…)을 올려라.

<<<PROMPT>>>
다음은 수신된 기관메일 1건이다. 이 메일에 대한 회신 초안을 작성하라.

[메일 제목]
{{SUBJECT}}

[발신자]
{{SENDER}}

[메일 본문]
{{BODY}}

[소유자 지시]
{{INSTRUCTION}}

- 소유자 지시가 있으면 그 지시가 회신 내용의 최우선 지침이다. 아래 작성 규칙과 충돌하면 소유자 지시를 따른다.
- 소유자 지시가 "(별도 지시 없음)"이면 아래 작성 규칙만 따른다.

회신 작성 규칙:
- 한국어 공식 서신체(존댓말)의 최종 문안을 작성한다. 이 문안은 수정 없이 그대로 발송된다.
- 본문은 8문장 이내로 간결하게.
- 형식적 상투구를 반복 나열하지 않는다 — 특히 "확인하겠습니다", "인지하였습니다", "별도로 회신드리겠습니다" 같은 문구를 기계적으로 늘어놓지 않는다.
- 내가 확실히 알 수 없는 사실(구체적 일정 확정, 금액, 승인 여부 등)은 단정하지 않는다.
- 이름/직함/전화번호 등 서명 정보를 지어내지 않는다(발송 계정의 기본 서명이 붙는다).

## 문체 가이드
- **인사**: 첫 줄 "안녕하세요, [이름] [직함]님." (상대 이름/직함 확인 불가 시 "안녕하세요."). 둘째 줄 자기소개: 내부 상대에게 "<owner-name>입니다.", 외부 상대에게 "<owner-employer> <owner-name>입니다."
- **서두**: 수신 확인 상투구 없이 첫 단락에서 용건을 바로 제시한다. (예: "[항목]과 관련하여 [서류] 검토를 요청드립니다.")
- **본문**: 1~3문장 단위의 짧은 단락을 빈 줄로 구분한다. 숫자, 기간, 일정 등 구체 정보를 명시하며 목록은 하이픈(-) 불릿을 사용한다. (예: "- [날짜]: [작업] 완료 예정")
- **어미**: "~를 요청드립니다", "~하였습니다", "~하도록 하겠습니다", "~해 드립니다", "~(주)시면 감사하겠습니다"를 사용한다. 부드러운 제안은 "~하면 좋을 것 같습니다"를 쓴다.
- **금지**: "확인하겠습니다", "인지하였습니다" 등 형식적 문구 나열을 금한다. 유보는 "확인되는 대로 다시 전달 드리겠습니다"처럼 자연스럽게 표현한다.
- **맺음**: 상대의 다음 액션을 청하는 문장 1개 → "감사합니다." → "<owner-name> 올림" 순으로 고정한다. (예: "검토하신 후 의견 주시면 감사하겠습니다.")
- **분량**: 핵심 위주로 수신 메일의 요구에 직접 답하며 8문장 이내를 유지한다.

다른 설명 없이 아래 형태의 JSON 객체 하나만 출력하라:
{"subject": "Re: ...", "body": "..."}
