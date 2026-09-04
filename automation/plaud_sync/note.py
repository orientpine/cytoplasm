"""PLAUD lifelog note planning and rendering — v2 layout (B안, 2026-09-04).

frontmatter → (# 제목 은 obsidian_write.render_note 가 올린다) → ## 한눈에 → ## 요약 →
## 결정 · 할 일(있을 때만) → ## 전문(접힌 callout) → --- 출처. The body starts with the
YAML block on purpose: the body is what the owner's ✅ binds (body_sha256 → action_hash),
so the frontmatter is approved with everything else.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import PurePosixPath
from typing import Final

from automation.obsidian_write.note import NotePlan
from automation.typing_compat import override

from .lifelog_fields import (
    DECISIONS_HEADING,
    DEFAULT_TIMEZONE,
    GLANCE_HEADING,
    NO_SUMMARY,
    SOURCE_RULE,
    SUMMARY_HEADING,
    TAG_ROOT,
    TRANSCRIPT_HEADING,
    decisions_block,
    frontmatter,
    glance_lines,
    lifelog_sections,
    local_stamp,
    render_duration,
    strip_unresolvable_images,
    topic_tags,
    transcript_block,
    unquote_transcript,
)
from .lifelog_model import ExtractionOutcome, LifelogRecording

__all__ = [
    "SOURCE_RULE",
    "SUMMARY_HEADING",
    "TRANSCRIPT_HEADING",
    "LifelogRecording",
    "PlaudNoteError",
    "note_title",
    "plan_lifelog_note",
    "recording_stamp",
    "render_lifelog_body",
    "split_lifelog_body",
]

_LIFELOG_ROOT: Final = PurePosixPath("000_PARA/Area/Lifelog")
_SLUG_LIMIT: Final = 60
_DIGEST_LENGTH: Final = 12
_DATE_PREFIX_RE: Final = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|\d{8}|\d{4}_\d{2}_\d{2})[-_T]?")


@dataclass(frozen=True, slots=True)
class PlaudNoteError(Exception):
    """Raised when a recording has no usable timestamp for note placement."""

    recording_id: str

    @override
    def __str__(self) -> str:
        return f"PLAUD recording {self.recording_id!r} has no valid timestamp"


def recording_stamp(recording: LifelogRecording, tz: tzinfo = DEFAULT_TIMEZONE) -> datetime:
    """Recording start in the note's zone; the one place an unusable timestamp fails."""
    stamp = local_stamp(recording, tz)
    if stamp is None:
        raise PlaudNoteError(recording.id)
    return stamp


def note_title(recording: LifelogRecording, stamp: datetime) -> str:
    return f"{_normalized_text(recording.name) or '녹음'} ({stamp.date().isoformat()})"


def plan_lifelog_note(
    recording: LifelogRecording, *, extraction: ExtractionOutcome, tz: tzinfo = DEFAULT_TIMEZONE
) -> NotePlan:
    """Build the deterministic PARA destination and content for one recording."""
    stamp = recording_stamp(recording, tz)
    slug = _slug_for_name(_normalized_text(recording.name))
    digest = hashlib.sha256(recording.id.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]
    filename = f"{stamp.date().isoformat()}-{slug}--{digest}.md"
    return NotePlan(
        relpath=_LIFELOG_ROOT / str(stamp.year) / filename,
        title=note_title(recording, stamp),
        body=_render(recording, extraction, stamp),
    )


def render_lifelog_body(
    recording: LifelogRecording, *, extraction: ExtractionOutcome, tz: tzinfo = DEFAULT_TIMEZONE
) -> str:
    """Render the v2 Markdown body (frontmatter first) for one recording."""
    return _render(recording, extraction, recording_stamp(recording, tz))


def _render(recording: LifelogRecording, extraction: ExtractionOutcome, stamp: datetime) -> str:
    summary = strip_unresolvable_images(recording.summary_markdown)
    topics = topic_tags(summary)
    parts = [
        frontmatter(
            tags=(TAG_ROOT, *topics),
            title=note_title(recording, stamp),
            source=f"PLAUD 녹음 {recording.id}",
            stamp=stamp,
        ),
        GLANCE_HEADING,
        "\n".join(glance_lines(recording, extraction, stamp=stamp, topics=topics, summary=summary)),
        SUMMARY_HEADING,
        summary or NO_SUMMARY,
    ]
    decisions = decisions_block(extraction)
    if decisions:
        parts += [DECISIONS_HEADING, decisions]
    source_timestamp = recording.start_at or recording.created_at
    source_line = (
        f"출처: PLAUD 녹음 {recording.id} · {source_timestamp} · {render_duration(recording.duration_ms)}"
    )
    if recording.transcript_source:
        source_line += f" · 전사: {recording.transcript_source}"
    parts += [TRANSCRIPT_HEADING, transcript_block(recording.transcript_text), SOURCE_RULE, source_line]
    return "\n\n".join(parts)


def split_lifelog_body(body: str) -> tuple[str, str]:
    """(summary, transcript) read back from a rendered body — v1 and v2 alike.

    Splits on the note's own heading lines only; a Plaud summary carries its own
    '## ' sub-headings and '------------' rules (2026-09-02 실측). The transcript comes
    back without the v2 callout prefix so a round trip returns the recording's text.
    """
    sections = lifelog_sections(body)
    return sections.get(SUMMARY_HEADING, ""), unquote_transcript(sections.get(TRANSCRIPT_HEADING, ""))


def _normalized_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _slug_for_name(name: str) -> str:
    allowed = "".join(
        character
        for character in name
        if character.isalnum() or character in {" ", "-", "_"}
    )
    slug = "-".join(allowed.split())[:_SLUG_LIMIT].strip("-_")
    slug = _DATE_PREFIX_RE.sub("", slug).strip("-_")
    return slug or "recording"
