"""회의록 본문 용어 교정 — 무엇을 고치고 무엇에는 손대지 않는가.

교정은 산출 문서(회의록)를 만들 때 일어난다(소유자 결정 2026-09-05). 그래서 이 모듈이
고치는 것은 **사람이 읽으라고 새로 쓴 문장**뿐이다: 전사본에서 그대로 따 온 근거(`basis`)와
원장 키(`ResolvedAction.id`)·화자 라벨·날짜는 증거이거나 키라서 고치면 회의록이 가리키는
대상 자체가 바뀐다.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "meeting" / "scripts"))

import meeting_terms  # noqa: E402
from meeting_types import (  # noqa: E402
    ActionItem,
    Decision,
    Extraction,
    MeetingHeader,
    NextMeeting,
    OpenQuestion,
    ResolvedAction,
    SpeakerRef,
    Topic,
)

#: 바른 용어 한 칸만 적힌 참고 문서와 같다 — 틀린 표기는 자모 거리로 찾는다.
GLOSSARY = (("한전기술", "한전기술"),)
#: 전사본에서 그대로 따 온 문장 — 오인식이 살아 있어야 한다.
TRANSCRIPT = "화자1: 항정기술 쪽에서 계측 자료를 받았습니다"


def test_the_summary_and_the_decision_are_corrected_while_the_quoted_transcript_is_not():
    """본문은 고치고, 그 본문이 인용한 원문은 그대로 둔다."""
    extraction = Extraction(
        decisions=(Decision("항정기술 계측 자료를 8월에 받는다", TRANSCRIPT),),
        summary=("항정기술 협의 결과를 정리했다",),
    )

    corrected, corrections = meeting_terms.correct(extraction, GLOSSARY)

    assert corrected.summary == ("한전기술 협의 결과를 정리했다",)
    assert corrected.decisions[0].text == "한전기술 계측 자료를 8월에 받는다"
    assert corrected.decisions[0].basis == TRANSCRIPT
    assert [(item.before, item.after) for item in corrections] == [
        ("항정기술", "한전기술"),
        ("항정기술", "한전기술"),
    ]


def test_action_items_keep_their_deadline_and_their_basis():
    """할 일·담당은 고치고 마감일과 근거는 그대로 — 날짜는 낱말이 아니다."""
    item = ActionItem("항정기술 자료 정리", "2026-07-24", TRANSCRIPT, owner="항정기술 담당자")
    extraction = Extraction(todos=(item,), others=(item,), milestones=(item,))

    corrected, _ = meeting_terms.correct(extraction, GLOSSARY)

    for fixed in (corrected.todos[0], corrected.others[0], corrected.milestones[0]):
        assert fixed.title == "한전기술 자료 정리"
        assert fixed.owner == "한전기술 담당자"
        assert fixed.deadline == "2026-07-24"
        assert fixed.basis == TRANSCRIPT


def test_the_discussion_the_open_questions_and_the_next_meeting_are_corrected():
    extraction = Extraction(
        discussion=(Topic("항정기술 계측", ("항정기술 자료 확인",), TRANSCRIPT),),
        open_questions=(OpenQuestion("항정기술 회신 여부", "항정기술 김민수", TRANSCRIPT),),
        next_meeting=NextMeeting("2026-07-22 10:00", "항정기술 자료 공유"),
    )

    corrected, _ = meeting_terms.correct(extraction, GLOSSARY)

    assert corrected.discussion[0].topic == "한전기술 계측"
    assert corrected.discussion[0].points == ("한전기술 자료 확인",)
    assert corrected.discussion[0].basis == TRANSCRIPT
    assert corrected.open_questions[0].title == "한전기술 회신 여부"
    assert corrected.open_questions[0].owner == "한전기술 김민수"
    assert corrected.open_questions[0].basis == TRANSCRIPT
    assert corrected.next_meeting == NextMeeting("2026-07-22 10:00", "한전기술 자료 공유")


def test_the_header_carries_the_corrected_names_and_the_untouched_date():
    """머리말도 회의록 본문이다 — 참석자·장소는 사람이 읽는 문장이고 날짜는 값이다."""
    extraction = Extraction(
        meeting=MeetingHeader(
            title="항정기술 협의",
            date="2026-07-15",
            attendees=("항정기술 박", "차"),
            place="항정기술 본사",
        )
    )

    corrected, _ = meeting_terms.correct(extraction, GLOSSARY)

    assert corrected.meeting.title == "한전기술 협의"
    assert corrected.meeting.attendees == ("한전기술 박", "차")
    assert corrected.meeting.place == "한전기술 본사"
    assert corrected.meeting.date == "2026-07-15"


def test_the_management_number_and_the_speaker_label_are_never_touched():
    """원장 키와 화자 라벨은 낱말이 아니라 키다 — 고치면 가리키는 대상이 바뀐다.

    `id` 는 어느 action item 이 닫혔는지를 정하고, `label` 은 전사본 본문의 `화자N` 과
    맞물려야 한다. 그래서 교정 가능한 모양이어도 지나간다.
    """
    extraction = Extraction(
        resolved_actions=(ResolvedAction("항정기술-25-001", TRANSCRIPT),),
        speakers=(SpeakerRef("화자1", "김민서", TRANSCRIPT),),
    )

    corrected, _ = meeting_terms.correct(extraction, (*GLOSSARY, ("김민수", "김민수")))

    assert corrected.resolved_actions[0].id == "항정기술-25-001"
    assert corrected.resolved_actions[0].basis == TRANSCRIPT
    assert corrected.speakers[0].label == "화자1"
    assert corrected.speakers[0].name == "김민수"
    assert corrected.speakers[0].basis == TRANSCRIPT


def test_an_owner_paired_substitution_is_reported_as_exact():
    """소유자가 직접 짝지은 두 칸 행은 자모 판정을 거치지 않는다 — 신뢰도가 다르다."""
    extraction = Extraction(summary=("열기환기 점검 일정",))

    _corrected, corrections = meeting_terms.correct(extraction, (("열기환기", "열교환기"),))

    assert [(item.kind, item.after) for item in corrections] == [("exact", "열교환기")]


def test_an_empty_glossary_returns_the_extraction_itself():
    """고칠 근거가 없으면 아무것도 하지 않는다 — 사본조차 만들지 않는다."""
    extraction = Extraction(summary=("항정기술 협의 결과",))

    corrected, corrections = meeting_terms.correct(extraction, ())

    assert corrected is extraction
    assert corrections == ()
