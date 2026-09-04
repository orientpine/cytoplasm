"""Deterministic pieces of the v2 lifelog note (B안, 2026-09-04): frontmatter, 한눈에, 결정 · 할 일, 접힌 전문.

Pure text shaping. The frontmatter mirrors what the owner's Obsidian Linter leaves in
place (yaml-key-sort tags→title→source→created→modified, yaml-timestamp
'YYYY-MM-DDTHH:mm:ss', yaml-title = first H1, single-line arrays), so a freshly
pushed note is already lint-clean — verified headlessly against the real plugin
build (.omo/plans/plaud-lifelog-format-v2.md 결정 3·9). The Linter unquotes a
plain 'title' and double-quotes one that carries ': ', so 'yaml_scalar' quotes
only when YAML needs it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, tzinfo
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .lifelog_model import (
    ExtractionOutcome,
    ExtractionSkipped,
    LifelogExtraction,
    LifelogRecording,
)

DEFAULT_TIMEZONE: Final = ZoneInfo("Asia/Seoul")
TIMEZONE_ENV: Final = "PLAUD_SYNC_TIMEZONE"
GLANCE_HEADING: Final = "## 한눈에"
SUMMARY_HEADING: Final = "## 요약"
DECISIONS_HEADING: Final = "## 결정 · 할 일"
TRANSCRIPT_HEADING: Final = "## 전문"
SOURCE_RULE: Final = "---"
OWN_HEADINGS: Final = (GLANCE_HEADING, SUMMARY_HEADING, DECISIONS_HEADING, TRANSCRIPT_HEADING)
NO_SUMMARY: Final = "- (요약 없음)"
NO_TRANSCRIPT: Final = "- (전사 없음)"
TAG_ROOT: Final = "lifelog"
MAX_TOPIC_TAGS: Final = 8
ONE_LINE_CHARS: Final = 160
TIMESTAMP_FORMAT: Final = "%Y-%m-%dT%H:%M:%S"
TRANSCRIPT_CALLOUT: Final = "> [!quote]- 전문 펼치기 ({count} 발화)"

_WEEKDAYS: Final = ("월", "화", "수", "목", "금", "토", "일")
#: Plaud sub-headings that name a *kind* of section, not a topic — never a tag.
_GENERIC_HEADINGS: Final = frozenset(
    heading.casefold()
    for heading in (
        "개요", "요약", "결론", "결정", "결정 사항", "결정사항", "할 일", "할일", "액션 아이템",
        "핵심", "핵심 내용", "주요 내용", "주요 논의", "논의", "논의 사항", "후속 조치", "다음 단계",
        "회의 개요", "전체 요약", "한눈에", "전문", "action items", "action item", "summary",
        "overview", "conclusion", "conclusions", "next steps", "key points", "todo", "todos",
    )
)
_HEADING_RE: Final = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_HEADING_LINE_RE: Final = re.compile(r"^#{1,6}\s")
_RULE_RE: Final = re.compile(r"^(-{3,}|_{3,}|\*{3,})$")
_LIST_MARKER_RE: Final = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_IMAGE_RE: Final = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_UNRESOLVABLE_IMAGE_RE: Final = re.compile(r"!\[[^\]]*\]\((?!https?://)[^)]*\)")
_BLANK_RUN_RE: Final = re.compile(r"\n{3,}")
_SENTENCE_BREAK_RE: Final = re.compile(r"(?<=[.!?。])\s+")
_SPEAKER_RE: Final = re.compile(r"^\[[^\]]*? · ([^\]]+)\]", re.MULTILINE)
_YAML_LEAD_CHARS: Final = "#-?:,[]{}&*!|>'\"%@\x60"
_YAML_RESERVED: Final = frozenset({"true", "false", "null", "yes", "no", "on", "off", "~"})


def note_timezone(env: Mapping[str, str]) -> tuple[ZoneInfo, str | None]:
    """(zone, warning): PLAUD_SYNC_TIMEZONE else Asia/Seoul — a bad name is reported, never raised.

    Shared by the cron (discovery) and transcribe_live (finalize) so both stamps agree.
    """
    name = env.get(TIMEZONE_ENV, "").strip()
    if not name:
        return DEFAULT_TIMEZONE, None
    try:
        return ZoneInfo(name), None
    except (ZoneInfoNotFoundError, ValueError):
        return DEFAULT_TIMEZONE, f"unknown {TIMEZONE_ENV} {name!r}; using {DEFAULT_TIMEZONE.key}"


def strip_unresolvable_images(summary: str) -> str:
    """Drop image markdown whose target is not an absolute URL.

    A Plaud summary opens with a poster image keyed into Plaud's own storage
    (permanent/<user>/<file>/summary_poster/card_*.png). Obsidian resolves a
    relative target inside the vault, where it cannot exist (2026-09-04 실측).
    Lines that held only such images disappear; https:// images stay.
    """
    kept: list[str] = []
    for raw in summary.splitlines():
        line = _UNRESOLVABLE_IMAGE_RE.sub("", raw)
        if raw.strip() and not line.strip():
            continue
        kept.append(line.rstrip() if line != raw else line)
    return _BLANK_RUN_RE.sub("\n\n", "\n".join(kept)).strip()


def yaml_scalar(value: str) -> str:
    """Quote a plain scalar exactly the way the owner's Linter (escape char '"') would.

    Mirrors escapeStringIfNecessaryAndPossible of the vault build, verified headlessly
    against 61 titles (docs/qa/PLV2): a value holding '"' but no "'" is wrapped in single
    quotes; a value holding "'" or one YAML would misread (': ', ' #', leading special,
    reserved word) is double-quoted with backslash and '"' escaped; a plain value stays
    bare — the Linter *unquotes* a quoted plain title, so quoting defensively would never
    be lint-clean — and a value holding both quote kinds with nothing else to escape is
    left bare too (the Linter cannot wrap it without escaping, so it does not).
    """
    has_double = '"' in value
    has_single = "'" in value
    structural = (
        not value
        or value != value.strip()
        or ": " in value
        or value.endswith(":")
        or " #" in value
        or value[0] in _YAML_LEAD_CHARS
        or value.casefold() in _YAML_RESERVED
    )
    if has_double and not has_single:
        return f"'{value}'"
    if not structural and (has_double or not has_single):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def frontmatter(*, tags: tuple[str, ...], title: str, source: str, stamp: datetime) -> str:
    when = stamp.strftime(TIMESTAMP_FORMAT)
    return "\n".join(
        (
            "---",
            f"tags: [{', '.join(tags)}]",
            f"title: {yaml_scalar(title)}",
            f"source: {source}",
            f"created: {when}",
            f"modified: {when}",
            "---",
        )
    )


def local_stamp(recording: LifelogRecording, tz: tzinfo) -> datetime | None:
    """Recording start in the note's zone (naive, like the Linter writes it); None if unparsable."""
    for raw in (recording.start_at, recording.created_at):
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(tz).replace(tzinfo=None)
        return parsed
    return None


def render_duration(duration_ms: int) -> str:
    minutes, seconds = divmod(duration_ms // 1_000, 60)
    return f"{minutes}분 {seconds}초" if minutes else f"{seconds}초"


def speaker_count(transcript: str) -> int:
    return len({match.group(1).strip() for match in _SPEAKER_RE.finditer(transcript)})


def _tag_for(heading: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_/" else " " for ch in heading.lstrip("#"))
    return "-".join(cleaned.split()).strip("-_/")


def topic_tags(summary: str) -> tuple[str, ...]:
    """'lifelog/<소제목>' for each topical Plaud sub-heading, in order, generic kinds excluded."""
    tags: list[str] = []
    for match in _HEADING_RE.finditer(summary):
        heading = " ".join(match.group(1).split())
        if heading.casefold() in _GENERIC_HEADINGS:
            continue
        slug = _tag_for(heading)
        tag = f"{TAG_ROOT}/{slug}" if slug else ""
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= MAX_TOPIC_TAGS:
            break
    return tuple(tags)


def one_line(summary: str) -> str:
    """The first sentence of the summary's first text line (headings, rules, images skipped)."""
    for raw in summary.splitlines():
        line = raw.strip()
        if not line or line == NO_SUMMARY or _HEADING_LINE_RE.match(line) or _RULE_RE.match(line):
            continue
        line = _IMAGE_RE.sub("", _LIST_MARKER_RE.sub("", line)).strip()
        if not line:
            continue
        first = _SENTENCE_BREAK_RE.split(line)[0].strip()
        return first if len(first) <= ONE_LINE_CHARS else first[: ONE_LINE_CHARS - 1] + "…"
    return ""


def glance_lines(
    recording: LifelogRecording,
    extraction: ExtractionOutcome,
    *,
    stamp: datetime,
    topics: tuple[str, ...],
    summary: str,
) -> tuple[str, ...]:
    """Dataview inline fields (녹음 · 주제 · 사람 · 장소 · 한 줄) — absent fields are omitted, not blank."""
    recorded = f"{stamp:%Y-%m-%d} ({_WEEKDAYS[stamp.weekday()]}) {stamp:%H:%M} · {render_duration(recording.duration_ms)}"
    speakers = speaker_count(recording.transcript_text)
    lines = [f"- 녹음:: {recorded}" + (f" · 화자 {speakers}명" if speakers else "")]
    if topics:
        lines.append("- 주제:: " + " ".join(f"#{tag}" for tag in topics))
    if isinstance(extraction, ExtractionSkipped):
        lines.append(f"- 추출:: 생략 ({extraction.reason})")
    else:
        if extraction.people:
            lines.append("- 사람:: " + ", ".join(f"[[{person}]]" for person in extraction.people))
        if extraction.places:
            lines.append("- 장소:: " + ", ".join(extraction.places))
    headline = one_line(summary)
    if headline:
        lines.append(f"- 한 줄:: {headline}")
    return tuple(lines)


def _at(stamp: str) -> str:
    return f" [{stamp}]" if stamp else ""


def decisions_block(extraction: ExtractionOutcome) -> str:
    """'- 결정: …' then '- [ ] …' checklist; empty string when there is nothing to list."""
    if not isinstance(extraction, LifelogExtraction):
        return ""
    lines = [f"- 결정: {decision.text}{_at(decision.at)}" for decision in extraction.decisions]
    for todo in extraction.todos:
        meta = [part for part in (f"담당 {todo.owner}" if todo.owner else "", f"기한 {todo.due}" if todo.due else "") if part]
        line = f"- [ ] {todo.text}"
        if meta:
            line += " — " + " · ".join(meta)
        lines.append(line + _at(todo.at))
    return "\n".join(lines)


def transcript_block(transcript: str) -> str:
    """The transcript folded into a collapsed quote callout; the placeholder when empty."""
    text = transcript.strip()
    if not text:
        return NO_TRANSCRIPT
    lines = text.splitlines()
    count = sum(1 for line in lines if line.strip() and not _RULE_RE.match(line.strip()))
    quoted = "\n".join(f"> {line}" if line else ">" for line in lines)
    return TRANSCRIPT_CALLOUT.format(count=count) + "\n" + quoted


def unquote_transcript(text: str) -> str:
    """Inverse of 'transcript_block' — leaves a v1 (unquoted) transcript untouched."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("> [!quote]"):
        return text.strip()
    body = [line[2:] if line.startswith("> ") else line[1:] if line.startswith(">") else line for line in lines[1:]]
    return "\n".join(body).strip()


def lifelog_sections(body: str) -> dict[str, str]:
    """Our own H2 sections (first occurrence each) → stripped text; frontmatter and 출처 fall outside."""
    collected: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        if line in OWN_HEADINGS and line not in collected:
            current = line
            collected[current] = []
        elif current is not None:
            collected[current].append(line)
    sections: dict[str, str] = {}
    for heading, lines in collected.items():
        text = "\n".join(lines)
        if heading == TRANSCRIPT_HEADING:
            text = text.rsplit(f"\n{SOURCE_RULE}\n", 1)[0]
        sections[heading] = text.strip()
    return sections
