"""LLM 응답을 읽는 **관대한 파서**. 결과의 모양은 `meeting_types` 가 소유한다.

파서는 **fail-soft** 다 — 없는 키, 형이 틀린 값, v3 시절의 평문 문자열 decisions 를 모두
빈 값이나 등가 객체로 낮추고 예외를 던지지 않는다. LLM 출력은 스키마를 완벽히 지키지
않으며, 한 필드가 어긋났다고 회의록 전체를 잃는 것이 더 나쁘다. 여기서 던지는 예외는
JSON 객체 자체를 못 찾은 경우(`ExtractionParseError`) 하나뿐이다.
"""

from __future__ import annotations

import json
import re
from typing import Final

from meeting_types import (
    ActionItem,
    Decision,
    Extraction,
    ExtractionParseError,
    MeetingHeader,
    NextMeeting,
    OpenQuestion,
    ResolvedAction,
    SpeakerRef,
    Topic,
    map_extraction,
)

__all__ = [
    "ActionItem",
    "Decision",
    "Extraction",
    "ExtractionParseError",
    "MeetingHeader",
    "NextMeeting",
    "OpenQuestion",
    "ResolvedAction",
    "SpeakerRef",
    "Topic",
    "map_extraction",
    "parse_extraction",
]

#: 전사본 본문이 쓰는 라벨 그대로여야 한다 — `speaker_00` 같은 도구 원본 라벨이나
#: 지어낸 이름이 라벨 자리에 오면 회의록 범례가 본문의 어느 블록도 가리키지 못한다.
_SPEAKER_LABEL: Final = re.compile(r"화자\d+")


def _clean_deadline(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return None


def _clean_items(raw: object, *, with_owner: bool) -> tuple[ActionItem, ...]:
    if not isinstance(raw, list):
        return ()
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        items.append(
            ActionItem(
                title=title,
                deadline=_clean_deadline(entry.get("deadline")),
                basis=str(entry.get("basis") or "").strip(),
                owner=str(entry.get("owner") or "").strip() or None
                if with_owner
                else None,
            )
        )
    return tuple(items)


def _clean_strings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(text for text in (str(item).strip() for item in raw if not isinstance(item, (dict, list))) if text)


def _clean_decisions(raw: object) -> tuple[Decision, ...]:
    """Accept both the v4 object form and the v3 plain-string form."""
    if not isinstance(raw, list):
        return ()
    decisions = []
    for entry in raw:
        if isinstance(entry, dict):
            text = str(entry.get("text") or entry.get("title") or "").strip()
            basis = str(entry.get("basis") or "").strip()
        elif isinstance(entry, list):
            continue
        else:
            text, basis = str(entry).strip(), ""
        if text:
            decisions.append(Decision(text, basis))
    return tuple(decisions)


def _clean_header(raw: object) -> MeetingHeader:
    if not isinstance(raw, dict):
        return MeetingHeader()
    place = str(raw.get("place") or "").strip()
    return MeetingHeader(
        title=str(raw.get("title") or "").strip(),
        date=_clean_deadline(raw.get("date")),
        attendees=_clean_strings(raw.get("attendees")),
        place=place or None,
    )


def _clean_topics(raw: object) -> tuple[Topic, ...]:
    if not isinstance(raw, list):
        return ()
    topics = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        subject = str(entry.get("topic") or entry.get("title") or "").strip()
        if not subject:
            continue
        topics.append(
            Topic(subject, _clean_strings(entry.get("points")), str(entry.get("basis") or "").strip())
        )
    return tuple(topics)


def _clean_questions(raw: object) -> tuple[OpenQuestion, ...]:
    if not isinstance(raw, list):
        return ()
    questions = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or entry.get("question") or "").strip()
        if not title:
            continue
        owner = str(entry.get("owner") or "").strip()
        questions.append(OpenQuestion(title, owner or None, str(entry.get("basis") or "").strip()))
    return tuple(questions)


def _clean_resolved_actions(raw: object) -> tuple[ResolvedAction, ...]:
    if not isinstance(raw, list):
        return ()
    actions = []
    for entry in raw:
        if isinstance(entry, dict):
            action_id = str(entry.get("id") or "").strip().upper()
            basis = str(entry.get("basis") or "").strip()
        elif isinstance(entry, str):
            action_id, basis = entry.strip().upper(), ""
        else:
            continue
        if action_id:
            actions.append(ResolvedAction(action_id, basis))
    return tuple(actions)


def _clean_speakers(raw: object) -> tuple[SpeakerRef, ...]:
    """Keep only entries whose label is a transcript label; an unnamed label still counts.

    A label with no name is information — it says the transcript had a speaker the meeting
    never identified — so it survives with `name=None` instead of being dropped.
    """
    if not isinstance(raw, list):
        return ()
    speakers = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or "").strip()
        if not _SPEAKER_LABEL.fullmatch(label):
            continue
        name = str(entry.get("name") or "").strip()
        speakers.append(SpeakerRef(label, name or None, str(entry.get("basis") or "").strip()))
    return tuple(speakers)


def _clean_next_meeting(raw: object) -> NextMeeting | None:
    if not isinstance(raw, dict):
        return None
    when = str(raw.get("when") or raw.get("date") or "").strip()
    note = str(raw.get("note") or "").strip()
    return NextMeeting(when, note) if when or note else None


def parse_extraction(raw: str) -> Extraction:
    """Extract the first balanced JSON object from raw text and validate it."""
    start = raw.find("{")
    if start < 0:
        raise ExtractionParseError("no JSON object in LLM response")
    depth = 0
    end = -1
    in_string = False
    escape = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end < 0:
        raise ExtractionParseError("unbalanced JSON object in LLM response")
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as error:
        raise ExtractionParseError(f"invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ExtractionParseError("LLM response JSON is not an object")
    return Extraction(
        decisions=_clean_decisions(payload.get("decisions")),
        todos=_clean_items(payload.get("todos"), with_owner=False),
        milestones=_clean_items(payload.get("milestones"), with_owner=False),
        others=_clean_items(payload.get("others"), with_owner=True),
        meeting=_clean_header(payload.get("meeting")),
        summary=_clean_strings(payload.get("summary")),
        discussion=_clean_topics(payload.get("discussion")),
        open_questions=_clean_questions(payload.get("open_questions")),
        next_meeting=_clean_next_meeting(payload.get("next_meeting")),
        resolved_actions=_clean_resolved_actions(payload.get("resolved_actions")),
        speakers=_clean_speakers(payload.get("speakers")),
    )
