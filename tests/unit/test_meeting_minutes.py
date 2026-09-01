"""회의록 문서 서식 계약 — 연구 회의록 골격 + 근거 하단 배치 (소유자 지시 2026-08-26)."""

from __future__ import annotations

import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_action_db  # noqa: E402
import meeting_actions  # noqa: E402
import meeting_llm  # noqa: E402
import meeting_minutes  # noqa: E402
import meeting_template  # noqa: E402

NOW = datetime(2026, 8, 26, 16, 51, 0, tzinfo=ZoneInfo("Asia/Seoul"))
_MARKER = re.compile(r"\[근\d+\]")
_DEFINITION = re.compile(r"^- (\[근\d+\]) \S")


def _full_extraction() -> meeting_llm.Extraction:
    return meeting_llm.Extraction(
        meeting=meeting_llm.MeetingHeader(title="주간 연구 미팅", date="2026-07-15", attendees=("차", "박", "김"), place="공학관 402호"),
        summary=(
            "중간보고서 제출일이 8월 1일로 확정됐다",
            "학회 초록 초안은 차가 맡는다",
        ),
        decisions=(
            meeting_llm.Decision("중간보고서는 8월 1일 제출로 확정한다", "제출일을 8월 1일로 못박음"),
            meeting_llm.Decision("학회 초록 초안 담당은 차", "초안 담당은 차로 정리"),
        ),
        todos=(
            meeting_llm.ActionItem("데이터셋 사전 작성", "2026-07-24", "차: 데이터셋 사전 7/24까지 작성"),
            meeting_llm.ActionItem("IRB 변경신청 초안", None, "차: IRB 변경신청 초안"),
        ),
        milestones=(
            meeting_llm.ActionItem("중간보고서 제출", "2026-08-01", "제출 기한을 8월 첫날로 잡자는 발언"),
        ),
        others=(
            meeting_llm.ActionItem(
                "센서 펌웨어 코드 리뷰", "2026-07-22", "박이 7월 22일까지 진행", owner="박"
            ),
        ),
        discussion=(
            meeting_llm.Topic(
                "센서 캘리브레이션",
                ("드리프트가 3주 차부터 커진다", "온도 보정 계수를 다시 잰다"),
                "캘리브레이션 논의 구간",
            ),
        ),
        open_questions=(
            meeting_llm.OpenQuestion("IRB 심의 일정 확인 필요", "차", "일정 미정으로 남음"),
        ),
        next_meeting=meeting_llm.NextMeeting("2026-07-22 10:00", "펌웨어 리뷰 결과 공유"),
    )


def _render(extraction: meeting_llm.Extraction, **overrides) -> str:
    kwargs = {
        "label": "주간 연구 미팅",
        "kind": "md",
        "extraction": extraction,
        "original_text": "일시: 2026-07-15\n차: 데이터셋 사전 7/24까지 작성\n박: 펌웨어 리뷰",
        "sensitive": False,
        "ref": "a1b2c3d4",
        "now": NOW,
        "evidence_footer": "[E1] RAG/회의: meetings/previous.md (2026-08-14, path)",
        "slide_notes": ("kickoff.pdf (12쪽)",),
    }
    kwargs.update(overrides)
    return meeting_minutes.render(**kwargs)


def _split(document: str) -> tuple[str, str]:
    head, marker, tail = document.partition(meeting_minutes.APPENDIX_HEADING)
    assert marker, "문서에 부록 경계선이 없다"
    return head, tail


def _headings(document: str) -> list[str]:
    return [line for line in document.splitlines() if line.startswith("## ")]


# --- C1: 연구 회의록 골격 -----------------------------------------------------


def test_section_order_follows_the_researched_skeleton():
    document = _render(_full_extraction())
    assert _headings(document) == [
        "## 한눈에 보기",
        "## 결정사항",
        "## 액션 아이템",
        "## 마일스톤",
        "## 논의 요지",
        "## 미결·확인 필요",
        "## 다음 회의",
        meeting_minutes.APPENDIX_HEADING,
    ]


def test_header_table_dates_the_meeting_not_the_processing_clock():
    head, _ = _split(_render(_full_extraction()))
    assert "| 일시 | 2026-07-15 |" in head
    assert "| 일시 | 2026-08-26 |" not in head
    assert "| 참석 | 차, 박, 김 |" in head
    assert "| 장소 | 공학관 402호 |" in head
    assert "| 발표자료 | kickoff.pdf (12쪽) |" in head


def test_meeting_date_falls_back_to_the_processing_date_when_absent():
    extraction = meeting_llm.Extraction(
        decisions=(meeting_llm.Decision("결정", "근거"),),
    )
    head, _ = _split(_render(extraction, slide_notes=()))
    assert "| 일시 | 2026-08-26 |" in head


def test_action_items_render_as_one_table_with_owner_and_deadline():
    head, _ = _split(_render(_full_extraction()))
    table = head.split("## 액션 아이템")[1].split("## ")[0]
    assert "| 담당 | 할 일 | 마감 | 근거 |" in table
    rows = [line for line in table.splitlines() if line.startswith("| ") and "---" not in line]
    assert len(rows) == 4, f"헤더 1 + 내 항목 2 + 타인 항목 1 이어야 한다: {rows}"
    assert "| **나** | 데이터셋 사전 작성 | 2026-07-24 |" in table
    assert "| 박 | 센서 펌웨어 코드 리뷰 | 2026-07-22 |" in table
    assert "| **나** | IRB 변경신청 초안 | — |" in table


def test_empty_optional_sections_are_dropped_instead_of_printing_none():
    minimal = meeting_llm.Extraction(decisions=(meeting_llm.Decision("유일한 결정", "그 근거"),))
    document = _render(minimal, slide_notes=())
    head, _ = _split(document)
    assert _headings(document) == [
        "## 결정사항",
        "## 액션 아이템",
        meeting_minutes.APPENDIX_HEADING,
    ]
    assert "(없음)" not in head.split("## 액션 아이템")[0]
    assert "발표자료" not in head


def test_mandatory_action_section_says_none_explicitly_when_empty():
    minimal = meeting_llm.Extraction(decisions=(meeting_llm.Decision("d", "b"),))
    head, _ = _split(_render(minimal, slide_notes=()))
    assert "없음" in head.split("## 액션 아이템")[1]


def test_participant_centered_topic_never_becomes_a_section_heading():
    participant = "참석자 알파"
    topic = f"{participant} Action Items"
    extraction = meeting_llm.Extraction(
        meeting=meeting_llm.MeetingHeader(attendees=(participant, "참석자 베타")),
        discussion=(meeting_llm.Topic(topic, ("공동 검토 일정을 정한다",), "검토 논의"),),
    )

    head, _ = _split(_render(extraction, slide_notes=()))

    assert topic in head
    assert all(topic not in line for line in _headings(head))
    assert all(topic not in line for line in head.splitlines() if line.startswith("### "))


# --- C2: 근거는 하단, 본문은 깨끗 ---------------------------------------------


def test_body_carries_markers_only_and_never_the_evidence_text():
    extraction = _full_extraction()
    head, tail = _split(_render(extraction))
    assert "근거:" not in head
    bases = [item.basis for item in (*extraction.todos, *extraction.others, *extraction.milestones)]
    bases += [decision.basis for decision in extraction.decisions]
    for basis in bases:
        assert basis not in head, f"근거 원문이 본문에 샜다: {basis}"
        assert basis in tail, f"근거 원문이 부록에 없다: {basis}"


def test_every_marker_has_exactly_one_definition_and_no_definition_is_unused():
    head, tail = _split(_render(_full_extraction()))
    used = _MARKER.findall(head)
    defined = [
        matched.group(1)
        for matched in map(_DEFINITION.match, tail.splitlines())
        if matched is not None
    ]
    assert used, "본문에 근거 마커가 하나도 없다"
    assert len(used) == len(set(used)), f"같은 마커가 본문에 중복됐다: {used}"
    assert len(defined) == len(set(defined)), f"정의가 중복됐다: {defined}"
    assert sorted(used) == sorted(defined), (
        f"고아 마커={set(used) - set(defined)}, 미사용 정의={set(defined) - set(used)}"
    )


def test_marker_uses_the_house_bracket_style_not_footnote_syntax():
    assert meeting_minutes.evidence_marker(1) == "[근1]"
    assert "[^" not in _render(_full_extraction())


def test_transcript_and_knowledge_sources_sit_below_the_appendix_boundary():
    head, tail = _split(_render(_full_extraction()))
    assert "차: 데이터셋 사전 7/24까지 작성" not in head
    assert "차: 데이터셋 사전 7/24까지 작성" in tail
    assert "[E1]" not in head
    assert "[E1]" in tail


def test_appendix_orders_evidence_then_sources_then_transcript_in_one_document():
    _, tail = _split(_render(_full_extraction()))
    assert tail.index("### A.") < tail.index("### B.") < tail.index("### C.")
    assert tail.index("[근1]") < tail.index("[E1]")


def test_appendix_boundary_is_announced_so_the_reader_knows_the_minutes_ended():
    document = _render(_full_extraction())
    assert meeting_minutes.APPENDIX_HEADING == "## 부록 · 근거와 원문"
    assert "회의록 본문은 위에서 끝납니다" in document


# --- write_note 는 렌더된 문서를 파일 하나로 그대로 쓴다 ------------------------


def test_write_note_persists_exactly_the_rendered_document(tmp_path):
    note = meeting_actions.write_note(
        tmp_path,
        label="주간 연구 미팅",
        kind="md",
        original_text="차: 데이터셋 사전 7/24까지 작성",
        extraction=_full_extraction(),
        sensitive=False,
        ref="a1b2c3d4",
        now=NOW,
        evidence_footer="[E1] RAG/회의: meetings/previous.md (2026-08-14, path)",
        slide_notes=("kickoff.pdf (12쪽)",),
    )
    assert [path.name for path in tmp_path.iterdir()] == [note.name], "사이드카 파일이 생겼다"
    content = note.read_text(encoding="utf-8")
    assert content.startswith("---\n"), "W2-4 색인용 frontmatter 가 사라졌다"
    assert meeting_minutes.APPENDIX_HEADING in content
    assert note.stat().st_mode & 0o777 == 0o600


def test_sensitive_note_uses_the_same_skeleton_and_keeps_the_original(tmp_path):
    extraction = meeting_llm.Extraction(
        decisions=(meeting_llm.Decision("청구항 범위 확정", "청구항 논의"),)
    )
    note = meeting_actions.write_note(
        tmp_path, label="민감 회의", kind="md", original_text="청구항 1항을 넓힌다",
        extraction=extraction, sensitive=True, ref="deadbeef", now=NOW,
    )
    content = note.read_text(encoding="utf-8")
    assert "patent-sensitive" in content
    assert "청구항 1항을 넓힌다" in content.split(meeting_minutes.APPENDIX_HEADING)[1]


# --- v4 스키마 파싱 (하위호환 포함) --------------------------------------------


def test_v4_payload_parses_every_new_key():
    raw = """{
      "meeting": {"title": "주간 미팅", "date": "2026-07-15",
                  "attendees": ["차", "박"], "place": "402호"},
      "summary": ["제출일 확정", "초안 담당 지정"],
      "decisions": [{"text": "8월 1일 제출", "basis": "제출일 확정 발언"}],
      "todos": [{"title": "사전 작성", "deadline": "2026-07-24", "basis": "7/24까지"}],
      "milestones": [], "others": [],
      "discussion": [{"topic": "캘리브레이션", "points": ["드리프트 증가"], "basis": "논의 구간"}],
      "open_questions": [{"title": "IRB 일정", "owner": "차", "basis": "미정"}],
      "next_meeting": {"when": "2026-07-22 10:00", "note": "리뷰 공유"}
    }"""
    extraction = meeting_llm.parse_extraction(raw)
    assert extraction.meeting.date == "2026-07-15"
    assert extraction.meeting.attendees == ("차", "박")
    assert extraction.summary == ("제출일 확정", "초안 담당 지정")
    assert extraction.decisions == (meeting_llm.Decision("8월 1일 제출", "제출일 확정 발언"),)
    assert extraction.discussion[0].points == ("드리프트 증가",)
    assert extraction.open_questions[0].owner == "차"
    assert extraction.next_meeting.when == "2026-07-22 10:00"


def test_v3_plain_string_decisions_still_parse():
    extraction = meeting_llm.parse_extraction('{"decisions": ["그냥 문자열"], "todos": []}')
    assert extraction.decisions == (meeting_llm.Decision("그냥 문자열", ""),)


def test_missing_v4_keys_are_empty_not_an_error():
    extraction = meeting_llm.parse_extraction('{"todos": [{"title": "t", "basis": "b"}]}')
    assert extraction.summary == () and extraction.discussion == ()
    assert extraction.open_questions == () and extraction.next_meeting is None
    assert extraction.meeting.date is None and extraction.meeting.attendees == ()


def test_malformed_v4_values_degrade_instead_of_raising():
    extraction = meeting_llm.parse_extraction(
        '{"meeting": "문자열", "summary": {"a": 1}, "discussion": [3], '
        '"open_questions": "x", "next_meeting": [], "decisions": [{"basis": "b"}]}'
    )
    assert extraction.meeting.date is None
    assert extraction.summary == () and extraction.discussion == ()
    assert extraction.open_questions == () and extraction.next_meeting is None
    assert extraction.decisions == ()


# --- per-project form layout + action-item tail --------------------------------


def _form() -> meeting_template.Template:
    return meeting_template.Template("", tuple(meeting_template.Section(*value) for value in (("4.", "Action Item 종합", "actions", 1), ("1.", "회의 정보", "meta", 1), ("2.", "참석자", "attendees", 1), ("3.", "결정사항", "decisions", 1), ("가.", "기타", "other", 2))))


_ACTION_SECTIONS = meeting_action_db.render_sections(outstanding=(), created=())


def test_templated_render_uses_the_form_labels_and_order():
    document = _render(_full_extraction(), template=_form(), action_sections=_ACTION_SECTIONS)
    assert _headings(document) == ["## 4. Action Item 종합", "## 1. 회의 정보", "## 2. 참석자", "## 3. 결정사항", "## 한눈에 보기", "## 논의 요지", "## 마일스톤", "## 미결·확인 필요", "## 다음 회의", meeting_minutes.APPENDIX_HEADING]
    assert _full_extraction().decisions[0].text in document


def test_templated_empty_section_prints_its_required_placeholder():
    assert "### 가. 기타\n\n- (해당 없음)" in _render(_full_extraction(), template=_form(), action_sections=_ACTION_SECTIONS)


def test_action_tail_closes_the_body_with_both_action_tables():
    document = _render(_full_extraction(), action_sections=_ACTION_SECTIONS)
    assert document.index("## 다음 회의") < document.index("## Action Item 종합") < document.index(meeting_minutes.APPENDIX_HEADING)
    assert "### 가. 미결 Action Items" in document and "### 나. 신규 Action Items" in document


def test_default_layout_without_an_action_tail_is_byte_identical_to_the_fixture():
    document = _render(_full_extraction())
    assert hashlib.sha256(document.encode()).hexdigest() == "2084bbe828e2e78c344137b7c4c066e52fe003de0a54ec82e1317e0d2567e77d"


def test_templated_layout_keeps_every_evidence_marker_integral():
    head, tail = _split(_render(_full_extraction(), template=_form(), action_sections=_ACTION_SECTIONS))
    used = _MARKER.findall(head)
    defined = [match.group(1) for match in map(_DEFINITION.match, tail.splitlines()) if match]
    assert used and len(used) == len(set(used)) and sorted(used) == sorted(defined)


_OWNER_FORM = """해양고신뢰성 과제 기술회의 회의록

1. 일시 및 장소 :

2. 참석자 :

3. 회의 내용
가. 안건
  ○

4. Action Item 종합
가. 미결 Action Items
나. 신규 Action Items
"""

_FILLED_SECTIONS = meeting_action_db.render_sections(
    outstanding=(meeting_action_db.Record("HOG26001", "해양고신뢰성", "유동 상관성 보고서", "한국전력기술", "2026-09-30", meeting_action_db.OPEN, "2026-08-20", "note.md", "", "", "근거"),),
    created=(meeting_action_db.Record("HOG26002", "해양고신뢰성", "열수력 해석 조건", "나", "2026-08-31", meeting_action_db.OPEN, "2026-08-20", "note.md", "", "", "근거"),),
)


def test_form_naming_action_subsections_renders_each_table_exactly_once():
    """소유자 실제 양식은 4./가./나. 세 절이 모두 액션이다 — 표가 세 번 반복되면 안 된다."""
    head, _ = _split(_render(_full_extraction(), template=meeting_template.parse(_OWNER_FORM), action_sections=_FILLED_SECTIONS))
    assert head.count("| 관리번호 | 내용 | 조치기한 | 담당기관 |") == 2
    assert head.count("HOG26001") == 1 and head.count("HOG26002") == 1
    assert head.count("### 가. 미결 Action Items") == 1
    assert head.count("### 나. 신규 Action Items") == 1


def test_form_parent_section_carries_its_children_not_a_placeholder():
    head, _ = _split(_render(_full_extraction(), template=meeting_template.parse(_OWNER_FORM), action_sections=_FILLED_SECTIONS))
    assert "## 4. Action Item 종합\n\n### 가. 미결 Action Items" in head
    assert "## 3. 회의 내용\n\n### 가. 안건" in head
