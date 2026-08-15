# meeting-extraction-v1

W2-3 회의록 구조화 추출 프롬프트. `skills/meeting/scripts/meeting_llm.py`가

> **SUPERSEDED by meeting-extraction-v2.md** — openai-codex(gpt-5.4) 백엔드에서
> 이 구조(지시문+스키마 예시 선행)가 프롬프트 에코를 유발함. 현행은 v2.
`<<<PROMPT>>>` 아래 본문을 읽어 `{{MY_NAMES}}`, `{{MEETING_TEXT}}`를 치환해
단일 user 메시지로 전송한다. 이 파일이 프롬프트의 단일 진실 원천이며,
변경 시 버전 파일명을 올린다(meeting-extraction-v2.md …).

<<<PROMPT>>>
당신은 회의록에서 실행 항목을 추출하는 도구입니다. 아래 회의록을 읽고
반드시 **JSON 객체 하나만** 출력하세요. 마크다운 펜스·설명·주석 금지.

스키마:
{
  "decisions": ["회의에서 확정된 결정사항 문장", ...],
  "todos": [{"title": "내가 해야 할 액션아이템", "deadline": "YYYY-MM-DD 또는 null", "basis": "근거가 된 회의록 원문 요지"}, ...],
  "milestones": [{"title": "프로젝트 마일스톤/데드라인 이벤트", "deadline": "YYYY-MM-DD 또는 null", "basis": "근거"}, ...],
  "others": [{"owner": "담당자 이름", "title": "타인 담당 액션아이템", "deadline": "YYYY-MM-DD 또는 null", "basis": "근거"}, ...]
}

규칙:
1. "나"는 다음 이름들 중 하나로 지칭됩니다: {{MY_NAMES}}. 이 이름(또는 명백히
   동일 인물)이 담당인 항목만 todos에 넣고, 다른 사람 담당은 others에 넣으세요.
2. 담당자가 불명확한 항목은 todos에 넣고 basis에 "담당 불명확"을 덧붙이세요.
3. milestones = 특정 날짜에 도달해야 하는 이벤트(제출 마감, 발표, 심사,
   중간점검 등). 반복 업무는 마일스톤이 아닙니다.
4. deadline은 회의록에 명시된 날짜만 사용하고, 연도가 없으면 회의 날짜
   기준으로 가장 가까운 미래를 가정하세요. 날짜가 전혀 없으면 null.
5. 회의록에 없는 내용을 지어내지 마세요. 각 항목의 basis는 회의록에 실제로
   존재하는 표현을 요약해야 합니다.
6. 출력은 UTF-8 JSON 객체 단 하나. 첫 문자는 `{`, 마지막 문자는 `}`.

회의록:
{{MEETING_TEXT}}
