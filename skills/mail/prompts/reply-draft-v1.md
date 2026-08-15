# mail reply draft prompt v1 (W4-2)

- Consumer: `skills/mail/scripts/triage_llm.py::draft_reply` — the pipeline's
  step ③. ALWAYS the non-GLM quality tier (openai-codex one-shot); the
  Korean final text never goes through GLM regardless of sensitivity.
- Contract: the response MUST contain exactly one JSON object with keys
  `subject` (string; may be empty → caller falls back to "Re: <원제목>")
  and `body` (non-empty string; the final Korean reply text). Parsed by
  `triage_core.parse_reply`.
- The draft is FINAL TEXT: it is sent verbatim after the owner's approval-DM
  ✅ — no post-editing pass exists. Keep it self-contained and safe.
- 변경 시 이 파일을 직접 고치지 말고 버전 파일명(v2, v3…)을 올려라.

<<<PROMPT>>>
다음은 수신된 기관메일 1건이다. 이 메일에 대한 회신 초안을 작성하라.

[메일 제목]
{{SUBJECT}}

[발신자]
{{SENDER}}

[메일 본문]
{{BODY}}

회신 작성 규칙:
- 한국어 공식 서신체(존댓말)의 최종 문안을 작성한다. 이 문안은 수정 없이 그대로 발송된다.
- 수신 메일이 요청한 사항에 대해: 확인이 필요한 사항은 확인하겠다고, 기한이 있는 사항은 기한을 인지했다고 답한다.
- 내가 확실히 알 수 없는 사실(구체적 일정 확정, 금액, 승인 여부 등)은 단정하지 말고 "확인 후 회신드리겠습니다" 형태로 유보한다.
- 마지막 줄은 "감사합니다."로 끝낸다. 이름/직함/전화번호 등 서명 정보를 지어내지 않는다(발송 계정의 기본 서명이 붙는다).
- 본문은 8문장 이내로 간결하게.

다른 설명 없이 아래 형태의 JSON 객체 하나만 출력하라:
{"subject": "Re: ...", "body": "..."}
