"""회의록 본문의 용어를 바로잡는다 — 부록의 전사본과 근거 인용에는 손대지 않는다.

교정이 일어나는 자리는 음성→전사본이 아니라 전사본→산출 문서다(소유자 결정 2026-09-05,
`docs/guide/용어-교정-규약.md`). 전사본은 증거라서 되돌릴 수 없는 치환을 새기지 않는다:
잘못 고친 낱말이 원문에 박히면 원래 표기는 어디에도 남지 않는다. 반대로 회의록 본문의
오인식은 그대로 두면 공정표·보고서로 옮겨 간다(2026-08-26 실측: 전사본의 '한정기술'이
외부 배포용 공정표 템플릿에 그대로 실렸다).

그래서 이 모듈은 **사람이 읽으라고 새로 쓴 문장**만 고친다. 지나가는 것은 셋이다:

- `basis` — 전사본에서 그대로 따 온 인용이다. 고치면 회의록이 인용한 원문이 원문이 아니다.
- `ResolvedAction.id` 와 `SpeakerRef.label` — 원장과 전사본 본문을 가리키는 키다.
  낱말처럼 생겼어도 고치면 어느 항목을 닫았는지, 어느 블록이 누구인지가 바뀐다.
- 날짜(`deadline`·`meeting.date`) — 값이지 낱말이 아니다.

판정 엔진은 `automation.term_correction` 하나뿐이다. 사본을 만들면 한쪽만 고쳐지고, 그때부터
같은 낱말이 문서마다 달라진다. 이 모듈은 입출력을 하지 않는다 — 참고 문서를 읽고 교정 내역을
남기는 일은 부작용을 소유한 `meeting_cli` 의 몫이다.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, TypeAlias

from meeting_runtime import runtime_root
from meeting_types import (
    ActionItem,
    Decision,
    Extraction,
    MeetingHeader,
    NextMeeting,
    OpenQuestion,
    SpeakerRef,
    Topic,
)

if TYPE_CHECKING:  # 릴리스 마운트에는 import 시점에 automation 이 없다 — 형만 빌려 온다.
    from automation.term_correction import Correction

Glossary: TypeAlias = Sequence[tuple[str, str]]
Apply: TypeAlias = Callable[[str, Glossary], "tuple[str, tuple[Correction, ...]]"]


def _engine():
    """`automation.term_correction` — 경로 해석은 `meeting_runtime` 하나가 소유한다.

    스킬은 릴리스 디렉터리에 복사되어 돌기 때문에 import 시점에 repo 가 sys.path 에 없다.
    `parents[N]` 깊이 추측은 라이브 마운트에서 죽었고 그 실패가 조용히 삼켜진 적이 있다.
    """
    root = str(runtime_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from automation import term_correction  # noqa: PLC0415 - lazy: 마운트 경로를 세운 뒤에만

    return term_correction


@dataclass(frozen=True, slots=True)
class _Fixer:
    """한 참고 문서를 모든 문장에 걸고, 바뀐 어절을 모아 둔다.

    카운트가 아니라 어절을 모으는 이유는 사후에 오탐을 되짚기 위해서다 — 몇 건인지는
    무엇이 무엇으로 바뀌었는지를 대답하지 못한다.
    """

    apply: Apply
    glossary: Glossary
    found: list[Correction]

    def text(self, value: str) -> str:
        fixed, corrections = self.apply(value, self.glossary)
        self.found.extend(corrections)
        return fixed

    def optional(self, value: str | None) -> str | None:
        """빈 값과 없는 값은 구별해서 그대로 돌려준다 — 담당 미정은 사실이다."""
        return self.text(value) if value else value


def _item(fix: _Fixer, value: ActionItem) -> ActionItem:
    """할 일 한 줄 — 마감일은 값이고 근거는 전사본 인용이라 둘 다 지나간다."""
    return ActionItem(
        fix.text(value.title), value.deadline, value.basis, fix.optional(value.owner)
    )


def _topic(fix: _Fixer, value: Topic) -> Topic:
    return Topic(fix.text(value.topic), tuple(fix.text(point) for point in value.points), value.basis)


def _question(fix: _Fixer, value: OpenQuestion) -> OpenQuestion:
    return OpenQuestion(fix.text(value.title), fix.optional(value.owner), value.basis)


def _header(fix: _Fixer, value: MeetingHeader) -> MeetingHeader:
    """머리말도 회의록 본문이다 — 참석자·장소는 사람이 읽는 문장이고 날짜는 값이다."""
    return MeetingHeader(
        fix.text(value.title),
        value.date,
        tuple(fix.text(name) for name in value.attendees),
        fix.optional(value.place),
    )


def correct(
    extraction: Extraction, glossary: Glossary
) -> tuple[Extraction, tuple[Correction, ...]]:
    """회의록에 실릴 문장만 고친 Extraction 과 바뀐 어절들을 함께 돌려준다.

    렌더된 문서가 아니라 **렌더 전 해석 결과**에 거는 이유가 이 함수의 존재 이유다: 문서
    전체에 걸면 부록의 원문 전사본까지 함께 고쳐져 증거가 사라진다.
    """
    if not glossary:
        return extraction, ()
    fix = _Fixer(_engine().apply, tuple(glossary), [])
    upcoming = extraction.next_meeting
    corrected = Extraction(
        decisions=tuple(
            Decision(fix.text(value.text), value.basis) for value in extraction.decisions
        ),
        todos=tuple(_item(fix, value) for value in extraction.todos),
        milestones=tuple(_item(fix, value) for value in extraction.milestones),
        others=tuple(_item(fix, value) for value in extraction.others),
        meeting=_header(fix, extraction.meeting),
        summary=tuple(fix.text(line) for line in extraction.summary),
        discussion=tuple(_topic(fix, value) for value in extraction.discussion),
        open_questions=tuple(_question(fix, value) for value in extraction.open_questions),
        next_meeting=(
            NextMeeting(fix.text(upcoming.when), fix.text(upcoming.note))
            if upcoming is not None
            else None
        ),
        # 관리번호와 그 근거는 원장의 키다 — 통째로 그대로 넘긴다.
        resolved_actions=extraction.resolved_actions,
        speakers=tuple(
            SpeakerRef(value.label, fix.optional(value.name), value.basis)
            for value in extraction.speakers
        ),
    )
    return corrected, tuple(fix.found)
