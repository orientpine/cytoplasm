# mail digest summary prompt v1 (W4-6)

- Consumer: `skills/mail/scripts/triage_llm.py::summarize` — one-line digest
  summaries. Routed by the step-1 sensitivity verdict (sensitive mail goes to
  the non-GLM tier, NEVER GLM).
- Contract: the response MUST contain exactly one JSON object
  `{"summary": "..."}` with a non-empty string value. Parsed by
  `triage_core.parse_digest_summary`.
- summary는 한국어 한 줄(80자 이내), 메일에 실제로 있는 사실만 담는다 —
  지어낸 세부사항 금지.
- 변경 시 이 파일을 직접 고치지 말고 버전 파일명(v2, v3…)을 올려라.

<<<PROMPT>>>
다음은 수신된 기관메일 1건이다. 이 메일의 핵심을 한 줄로 요약하라.

[메일 제목]
{{SUBJECT}}

[발신자]
{{SENDER}}

[메일 본문]
{{BODY}}

요약 규칙:
- 한국어 한 줄, 80자 이내.
- 메일에 실제로 있는 사실만 담는다. 없는 내용(일정, 금액, 결정 등)을 지어내지 않는다.

다른 설명 없이 아래 형태의 JSON 객체 하나만 출력하라:
{"summary": "..."}
