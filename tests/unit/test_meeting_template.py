"""Meeting-form outline parsing contracts."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_template  # noqa: E402

FORM = """APR1400 설계 유효원형 SMT 과제
'22.06월 기술회의 회의록

1. 일시 및 장소 : 2022.06.20.(월) 14:00 ~ 18:00 / 제2회의실

2. 참석자 : 중앙연구원, 한기, 두산, 기계연, 우진 과제책임자 및 담당자

3. 회의 내용
가. 유동 상관성 관련 용어 정리
  ○ 압력 PSD 변환, 상관길이 결정 등 과제 성격에 따라 필요한 용어 정의
나. 측정 및 해석 결과 처리를 위한 계산식 비교
  ○ PSD 계산식 및 window function 일치 확인
다. 기타
  ○ 해외자문 보고서 초안접수(6/30)

4. Action Item 종합
가. 미결 Action Items
나. 신규 Action Items
"""


def test_parse_owner_form_outline():
    template = meeting_template.parse(FORM)

    assert template is not None
    assert template.title == "APR1400 설계 유효원형 SMT 과제"
    assert [section.label for section in template.sections if section.level == 1] == [
        "1.",
        "2.",
        "3.",
        "4.",
    ]
    assert [section.slot for section in template.sections if section.level == 1] == [
        "meta",
        "attendees",
        "discussion",
        "actions",
    ]
    assert template.sections[0].title == "일시 및 장소"
    assert meeting_template.Section("가.", "유동 상관성 관련 용어 정리", "other", 2) in template.sections
    assert all("압력 PSD" not in section.title for section in template.sections)


def test_parse_rejects_prose_without_outline():
    assert meeting_template.parse("회의에서 일정과 담당자를 논의했습니다.") is None


def test_template_name_identifies_readable_forms():
    assert meeting_template.is_template_name("회의록양식.md")
    assert not meeting_template.is_template_name("2026-08-25_회의록-킥오프.md")
    assert not meeting_template.is_template_name("양식.hwp")


def test_heading_renders_level_one_section():
    section = meeting_template.Section("1.", "일시 및 장소", "meta", 1)

    assert meeting_template.heading(section) == "## 1. 일시 및 장소"


def test_action_subsections_classify_apart_from_their_parent():
    assert meeting_template.classify("Action Item 종합") == "actions"
    assert meeting_template.classify("미결 Action Items") == "actions_open"
    assert meeting_template.classify("신규 Action Items") == "actions_new"


def test_parent_section_with_a_deeper_child_prints_no_placeholder():
    template = meeting_template.Template(
        "",
        (
            meeting_template.Section("4.", "Action Item 종합", "actions", 1),
            meeting_template.Section("가.", "미결 Action Items", "actions_open", 2),
        ),
    )

    body = meeting_template.render_body(template, {"actions_open": ["- 항목"]})

    assert body[:2] == ["## 4. Action Item 종합", ""]
    assert "- (해당 없음)" not in body
