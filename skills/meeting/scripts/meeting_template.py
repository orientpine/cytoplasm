"""Parse the section outline from a plain-text meeting-minutes form."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable, Final, Mapping, Sequence

SLOTS: Final = (
    "meta",
    "attendees",
    "summary",
    "decisions",
    "discussion",
    "actions",
    "actions_open",
    "actions_new",
    "milestones",
    "open_questions",
    "next_meeting",
    "other",
)


@dataclass(frozen=True, slots=True)
class Section:
    label: str
    title: str
    slot: str
    level: int


@dataclass(frozen=True, slots=True)
class Template:
    title: str
    sections: tuple[Section, ...]


_LEVEL_ONE = re.compile(r"^\s*(?P<label>\d+[.)]|제\d+장)\s*(?P<title>.*)$")
_LEVEL_TWO = re.compile(
    r"^\s*(?P<label>[가나다라마바사아자차카타파하][.)]|[①-⑳]|[A-Za-z][.)])\s*(?P<title>.*)$"
)
_MARKDOWN_HEADING = re.compile(r"^\s*(?P<marks>#{1,6})\s+(?P<title>.*)$")
_TEMPLATE_TERMS: Final = ("양식", "서식", "템플릿", "template")
_READABLE_SUFFIXES: Final = (".md", ".markdown", ".txt")
_CLASSIFICATIONS: Final = (
    ("actions_open", ("미결 action", "미결액션", "미결 액션", "미결 조치", "미결 항목")),
    ("actions_new", ("신규 action", "신규액션", "신규 액션", "신규 조치", "신규 항목")),
    ("actions", ("action item", "액션", "조치사항")),
    ("attendees", ("참석", "참여자", "배석")),
    ("meta", ("일시", "장소", "개요", "회의 정보")),
    ("decisions", ("결정", "합의")),
    ("milestones", ("마일스톤", "추진일정", "일정")),
    ("open_questions", ("미결", "확인 필요", "미해결")),
    ("next_meeting", ("차기", "다음 회의", "향후 회의")),
    ("summary", ("요약", "한눈")),
    ("discussion", ("회의 내용", "논의", "토의", "안건", "주요 내용")),
)


def is_template_name(name: str) -> bool:
    """Return whether a readable Drive filename identifies a meeting form."""
    path = Path(name)
    return path.suffix.casefold() in _READABLE_SUFFIXES and any(
        term in path.stem.casefold() for term in _TEMPLATE_TERMS
    )


def classify(title: str) -> str:
    """Map a form section title to the first matching minutes slot."""
    normalized = " ".join(title.casefold().split())
    for slot, keywords in _CLASSIFICATIONS:
        if any(keyword in normalized for keyword in keywords):
            return slot
    return "other"


def parse(text: str) -> Template | None:
    """Return a form outline, or ``None`` when text is not an outline."""
    title: str | None = None
    sections: list[Section] = []
    for line in text.splitlines():
        stripped = line.strip()
        level_one = _LEVEL_ONE.match(line)
        level_two = _LEVEL_TWO.match(line)
        if title is None and stripped and not level_one and not level_two:
            title = stripped
        if level_one:
            sections.append(_section(level_one["label"], level_one["title"], 1))
            continue
        if level_two:
            sections.append(_section(level_two["label"], level_two["title"], 2))
            continue
        markdown = _MARKDOWN_HEADING.match(line)
        if markdown:
            sections.append(_section("", markdown["title"], min(len(markdown["marks"]), 2)))
    if len(sections) < 2:
        return None
    return Template(title or "", tuple(sections))


def heading(section: Section) -> str:
    """Return the markdown heading that represents a parsed section."""
    content = " ".join(part for part in (section.label, section.title) if part)
    return f"{'##' if section.level == 1 else '###'} {content}"


_FAMILIES: Final = {"actions": frozenset({"actions", "actions_open", "actions_new"})}


def _covered(sections: Sequence[Section], index: int) -> bool:
    """Whether deeper sections beneath this one already carry its content.

    Only then is a section a pure container. `4. Action Item 종합` above `가. 미결`/
    `나. 신규` is covered and must stay empty or both tables print twice; `3. 결정사항`
    above an unrelated `가. 기타` is NOT covered, and emptying it would delete the
    decisions the meeting actually made.
    """
    section = sections[index]
    family = _FAMILIES.get(section.slot, frozenset({section.slot}))
    for other in sections[index + 1:]:
        if other.level <= section.level:
            return False
        if other.slot in family:
            return True
    return False


def carried_slots(template: Template) -> set[str]:
    """Slots the form actually prints content into."""
    sections = template.sections
    return {
        section.slot
        for index, section in enumerate(sections)
        if not _covered(sections, index)
    }


def strip_heading(lines: Sequence[str]) -> list[str]:
    """A rendered section's body, without the heading the form supplies itself."""
    return list(lines[2:-1]) if lines else []


def render_body(template: Template, blocks: Mapping[str, Sequence[str]]) -> list[str]:
    """Lay out supplied minutes blocks in the owner's form order.

    A covered section (see ``_covered``) prints its heading alone; every other section
    prints its block, or "해당 없음" when the form asked for a section the meeting has
    nothing for.
    """
    lines: list[str] = []
    sections = template.sections
    for index, section in enumerate(sections):
        covered = _covered(sections, index)
        content = [] if covered else list(blocks.get(section.slot, ()) or ["- (해당 없음)"])
        lines += [heading(section), "", *content, ""] if content else [heading(section), ""]
    return lines


def render_form_body(
    template: Template,
    *,
    header: Sequence[str],
    attendees: Sequence[str],
    action_sections: Sequence[str],
    split_action_sections: Callable[[Sequence[str]], Mapping[str, Sequence[str]]],
    summary: Callable[[], Sequence[str]],
    decisions: Callable[[], Sequence[str]],
    discussion: Callable[[], Sequence[str]],
    actions: Callable[[], Sequence[str]],
    milestones: Callable[[], Sequence[str]],
    open_questions: Callable[[], Sequence[str]],
    next_meeting: Callable[[], Sequence[str]],
) -> tuple[list[str], bool]:
    """Lay out rendered minutes content in a parsed owner form."""
    slots = {section.slot for section in template.sections}
    carried = carried_slots(template)
    split = split_action_sections(action_sections)

    # A slot only a covered parent claims is never printed, so do not render basis
    # markers for it; they would otherwise become orphan definitions in the appendix.
    def want(slot: str) -> bool:
        return slot in carried or slot not in slots

    blocks = {
        "meta": list(header[2:-1]),
        "attendees": [f"- {name}" for name in attendees],
        "summary": strip_heading(summary()) if want("summary") else [],
        "decisions": strip_heading(decisions()) if want("decisions") else [],
        "discussion": strip_heading(discussion()) if want("discussion") else [],
        "actions": strip_heading(action_sections if "actions" in slots else actions()) if want("actions") else [],
        "actions_open": split["actions_open"],
        "actions_new": split["actions_new"],
        "milestones": strip_heading(milestones()) if want("milestones") else [],
        "open_questions": strip_heading(open_questions()) if want("open_questions") else [],
        "next_meeting": strip_heading(next_meeting()) if want("next_meeting") else [],
        "other": [],
    }
    body = render_body(template, blocks)
    for slot, heading_text in (
        ("meta", "## 회의 정보"), ("attendees", "## 참석자"), ("summary", "## 한눈에 보기"),
        ("decisions", "## 결정사항"), ("discussion", "## 논의 요지"),
        ("actions", "## 액션 아이템"),
        ("milestones", "## 마일스톤"), ("open_questions", "## 미결·확인 필요"),
        ("next_meeting", "## 다음 회의"),
    ):
        if slot not in slots and blocks[slot]:
            body += [heading_text, "", *blocks[slot], ""]
    return body, bool(slots & {"actions", "actions_open", "actions_new"})


def _section(label: str, content: str, level: int) -> Section:
    title = _title(content)
    return Section(label, title, classify(title), level)


def _title(content: str) -> str:
    title, separator, remainder = content.strip().partition(":")
    if separator and remainder.strip():
        return title.strip().rstrip(":").rstrip()
    return content.strip().rstrip(":").rstrip()
