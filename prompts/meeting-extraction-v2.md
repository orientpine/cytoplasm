# meeting-extraction-v2


> **SUPERSEDED by meeting-extraction-v3.md** — "=== 마커 + 스키마 불릿" 구조도
> codex 에코를 유발함. 현행은 v3.
W2-3 회의록 구조화 추출 프롬프트 (현행). v1은 openai-codex(gpt-5.4) 백엔드에서
"지시문+스키마 예시 먼저" 구조가 프롬프트 에코를 유발해 폐기됨 — v2는
회의록 본문을 먼저, 출력 지시를 마지막에 둔다(양 프로바이더 실검증).
`skills/meeting/scripts/meeting_llm.py`가 `<<<PROMPT>>>` 아래 본문을 읽어
`{{MY_NAMES}}`, `{{MEETING_TEXT}}`를 치환해 단일 user 메시지로 전송한다.
변경 시 버전 파일명을 올린다(meeting-extraction-v3.md …).

<<<PROMPT>>>
아래 회의록을 읽어라.

=== 회의록 시작 ===
{{MEETING_TEXT}}
=== 회의록 끝 ===

위 회의록에서 다음 4개 키를 가진 JSON 객체 하나만 출력하라.
설명·주석·마크다운 펜스 금지. 출력의 첫 문자는 '{', 마지막 문자는 '}'.

- "decisions": 회의에서 확정된 결정사항 문자열 배열.
- "todos": 내 담당 액션아이템 배열. 각 원소는 {"title": 문자열, "deadline": "YYYY-MM-DD" 또는 null, "basis": 근거가 된 회의록 원문 요지}.
  "나"는 다음 이름 중 하나로 지칭된다: {{MY_NAMES}}. 담당자가 불명확한 항목도
  todos에 넣되 basis에 "담당 불명확"을 덧붙여라.
- "milestones": 특정 날짜에 도달해야 하는 이벤트(제출 마감, 발표, 심사, 중간점검 등)
  배열. 각 원소는 {"title", "deadline", "basis"}. 반복 업무는 제외.
- "others": 타인 담당 액션아이템 배열. 각 원소는 {"owner": 담당자 이름, "title", "deadline", "basis"}.

회의록에 없는 내용을 지어내지 마라. deadline은 회의록에 명시된 날짜만 사용하고,
연도가 없으면 회의 날짜 기준 가장 가까운 미래로 해석하라. 날짜가 없으면 null.
