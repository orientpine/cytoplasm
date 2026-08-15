# meeting-extraction-v3 (현행)

W2-3 회의록 구조화 추출 프롬프트. `skills/meeting/scripts/meeting_llm.py`가
`<<<PROMPT>>>` 아래 본문을 읽어 `{{MY_NAMES}}`, `{{MEETING_TEXT}}`를 치환해
단일 user 메시지로 전송한다. 변경 시 버전 파일명을 올린다.

버전 이력 (openai-codex gpt-5.4 원샷 실검증):
- v1 폐기: "지시문+스키마 예시 선행" 구조 → codex가 프롬프트를 문서로 오인해 에코.
- v2 폐기: "=== 회의록 시작/끝 ===" 마커 + 스키마 불릿 나열 → 동일 에코 재발.
- v3 채택: 마커/스키마 블록 없는 평문 지시-마지막 구조 — glm-main과 codex 양쪽에서
  patent/clean 픽스처 파싱 성공 확인. codex 원샷은 반드시 `-t todo`(무해 툴셋)와
  함께 호출할 것 — 기본 툴셋이면 파일 편집 에이전트로 동작한다.

<<<PROMPT>>>
아래 회의록을 읽어라.

{{MEETING_TEXT}}

이제 이 회의록에서 decisions(회의에서 확정된 결정사항 문자열 배열), todos(내가 담당인 액션아이템, title/deadline/basis 키의 객체 배열), milestones(특정 날짜에 도달해야 하는 이벤트 — 제출 마감·발표·심사·중간점검 등, title/deadline/basis 키의 객체 배열, 반복 업무 제외), others(타인 담당 액션아이템, owner/title/deadline/basis 키의 객체 배열) 4개 키를 가진 JSON 객체 하나만 출력하라. "나"는 다음 이름 중 하나로 지칭된다: {{MY_NAMES}}. 담당자가 불명확한 항목은 todos에 넣고 basis에 "담당 불명확"을 덧붙여라. deadline은 회의록에 명시된 날짜만 YYYY-MM-DD로 쓰고(연도가 없으면 회의 날짜 기준 가장 가까운 미래), 날짜가 없으면 null. 회의록에 없는 내용을 지어내지 마라. basis는 근거가 된 회의록 원문 요지다. 설명 금지, 마크다운 펜스 금지, JSON 객체 하나만 출력하라.
