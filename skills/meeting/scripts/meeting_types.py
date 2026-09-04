"""회의록 추출 결과의 **모양** — 데이터클래스와 그 위의 순수 변환 하나.

`meeting_schema` 에서 갈라져 나온 것은 크기 때문이 아니라 질문이 다르기 때문이다:
여기는 "결과가 무엇으로 이루어져 있는가", 저기는 "LLM 이 흘린 JSON 을 어디까지 봐줄
것인가". 그래서 이 모듈은 파서를 import 하지 않고(순환 없음), `meeting_schema` 가 이
이름들을 전부 재수출하므로 기존 호출부는 그대로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


class ExtractionParseError(Exception):
    """Raised when the LLM response does not contain the required JSON."""


@dataclass(frozen=True, slots=True)
class ActionItem:
    """One extracted todo/milestone/other-owner item."""

    title: str
    deadline: str | None
    basis: str
    owner: str | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    """One confirmed decision plus the transcript line it rests on."""

    text: str
    basis: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedAction:
    """One existing action item confirmed as closed by the meeting."""

    id: str
    basis: str = ""


@dataclass(frozen=True, slots=True)
class SpeakerRef:
    """One transcript speaker label resolved to a person — `name` None means 미상.

    Guessing a name is worse than admitting ignorance: the minutes header names who said
    what, and a wrong name there propagates into artifacts authored from the minutes.
    """

    label: str
    name: str | None = None
    basis: str = ""


@dataclass(frozen=True, slots=True)
class MeetingHeader:
    """Metadata the minutes header shows — absent fields fall back at render time."""

    title: str = ""
    date: str | None = None
    attendees: tuple[str, ...] = ()
    place: str | None = None


@dataclass(frozen=True, slots=True)
class Topic:
    """One discussion thread, summarized by subject rather than by speaker turn."""

    topic: str
    points: tuple[str, ...] = ()
    basis: str = ""


@dataclass(frozen=True, slots=True)
class OpenQuestion:
    """Unresolved item — an action without an owner or a deadline belongs here."""

    title: str
    owner: str | None = None
    basis: str = ""


@dataclass(frozen=True, slots=True)
class NextMeeting:
    """Next session, when the meeting fixed one."""

    when: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class Extraction:
    """Validated extraction payload (v6 — every key past `others` is optional)."""

    decisions: tuple[Decision, ...] = ()
    todos: tuple[ActionItem, ...] = ()
    milestones: tuple[ActionItem, ...] = ()
    others: tuple[ActionItem, ...] = ()
    meeting: MeetingHeader = MeetingHeader()
    summary: tuple[str, ...] = ()
    discussion: tuple[Topic, ...] = ()
    open_questions: tuple[OpenQuestion, ...] = ()
    next_meeting: NextMeeting | None = None
    resolved_actions: tuple[ResolvedAction, ...] = ()
    speakers: tuple[SpeakerRef, ...] = ()


def map_extraction(extraction: Extraction, clean: Callable[[str], str]) -> Extraction:
    """Apply one citation-integrity transform to every generated text field.

    A resolved action's `id` is deliberately left alone: it is a key into the project's
    action-item database, not generated prose, and cleaning it would let a citation pass
    rewrite which item a meeting closed. A speaker's `label` is the same kind of key —
    it must keep matching the `화자N` written in the transcript body.
    """
    def item(value: ActionItem) -> ActionItem:
        return ActionItem(
            clean(value.title), value.deadline, clean(value.basis),
            clean(value.owner) if value.owner else None,
        )

    def topic(value: Topic) -> Topic:
        return Topic(
            clean(value.topic),
            tuple(clean(point) for point in value.points),
            clean(value.basis),
        )

    def question(value: OpenQuestion) -> OpenQuestion:
        return OpenQuestion(
            clean(value.title),
            clean(value.owner) if value.owner else None,
            clean(value.basis),
        )

    header = extraction.meeting
    return Extraction(
        tuple(Decision(clean(value.text), clean(value.basis)) for value in extraction.decisions),
        tuple(item(value) for value in extraction.todos),
        tuple(item(value) for value in extraction.milestones),
        tuple(item(value) for value in extraction.others),
        MeetingHeader(
            clean(header.title),
            header.date,
            tuple(clean(name) for name in header.attendees),
            clean(header.place) if header.place else None,
        ),
        tuple(clean(value) for value in extraction.summary),
        tuple(topic(value) for value in extraction.discussion),
        tuple(question(value) for value in extraction.open_questions),
        (
            NextMeeting(clean(extraction.next_meeting.when), clean(extraction.next_meeting.note))
            if extraction.next_meeting is not None
            else None
        ),
        tuple(ResolvedAction(value.id, clean(value.basis)) for value in extraction.resolved_actions),
        tuple(
            SpeakerRef(
                value.label,
                clean(value.name) if value.name else None,
                clean(value.basis),
            )
            for value in extraction.speakers
        ),
    )
