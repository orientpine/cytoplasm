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
from dataclasses import dataclass, replace
from datetime import datetime, tzinfo
from pathlib import PurePosixPath
from typing import Final

from automation import term_correction
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
from .lifelog_model import ExtractionOutcome, LifelogExtraction, LifelogRecording

__all__ = [
    "SOURCE_RULE",
    "SUMMARY_HEADING",
    "TRANSCRIPT_HEADING",
    "CorrectedNote",
    "LifelogRecording",
    "PlaudNoteError",
    "corrected_lifelog_note",
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


@dataclass(frozen=True, slots=True)
class CorrectedNote:
    """언 노트와 그때 바뀐 낱말들 — 감사 로그는 참고 문서를 읽어 온 쪽이 남긴다."""

    plan: NotePlan
    corrections: tuple[term_correction.Correction, ...] = ()


def recording_stamp(recording: LifelogRecording, tz: tzinfo = DEFAULT_TIMEZONE) -> datetime:
    """Recording start in the note's zone; the one place an unusable timestamp fails."""
    stamp = local_stamp(recording, tz)
    if stamp is None:
        raise PlaudNoteError(recording.id)
    return stamp


def note_title(recording: LifelogRecording, stamp: datetime) -> str:
    return corrected_title(recording, stamp, ())[0]


def corrected_title(
    recording: LifelogRecording, stamp: datetime, glossary: term_correction.Glossary
) -> tuple[str, tuple[term_correction.Correction, ...]]:
    """제목도 사람이 읽는 문장이라 고친다 — 그러나 **경로는 고치지 않는다**.

    Plaud 가 붙이는 녹음 이름은 음성에서 나오므로 본문과 같은 오인식을 안고 온다. 파일
    이름의 슬러그는 그 이름의 원문에서 계속 뽑는다: 경로가 참고 문서를 따라 움직이면
    용어집을 한 줄 고친 날 같은 녹음이 노트 둘로 갈라진다.
    """
    name, corrections = term_correction.apply(_normalized_text(recording.name) or "녹음", glossary)
    return f"{name} ({stamp.date().isoformat()})", corrections


def plan_lifelog_note(
    recording: LifelogRecording,
    *,
    extraction: ExtractionOutcome,
    tz: tzinfo = DEFAULT_TIMEZONE,
    glossary: term_correction.Glossary = (),
) -> NotePlan:
    """Build the deterministic PARA destination and content for one recording."""
    return corrected_lifelog_note(recording, extraction=extraction, tz=tz, glossary=glossary).plan


def corrected_lifelog_note(
    recording: LifelogRecording,
    *,
    extraction: ExtractionOutcome,
    tz: tzinfo = DEFAULT_TIMEZONE,
    glossary: term_correction.Glossary = (),
) -> CorrectedNote:
    """계획한 노트와 그 노트를 만들며 고친 낱말들.

    교정은 노트를 **얼리는 이 자리**에서 끝난다 — 승인 카드와 push 가 언 본문의 sha 를 묶으므로
    나중 교정은 존재할 수 없다. 무엇이 바뀌었는지는 돌려만 주고, 로그로 남기는 것은 참고 문서를
    읽어 온 효과 경계의 일이다(순수 함수는 파일을 쓰지 않는다).
    """
    stamp = recording_stamp(recording, tz)
    slug = _slug_for_name(_normalized_text(recording.name))
    digest = hashlib.sha256(recording.id.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]
    filename = f"{stamp.date().isoformat()}-{slug}--{digest}.md"
    body, corrections = _render(recording, extraction, stamp, glossary)
    return CorrectedNote(
        plan=NotePlan(
            relpath=_LIFELOG_ROOT / str(stamp.year) / filename,
            # 본문이 이미 같은 제목을 실었으므로 교정 내역은 거기서 한 번만 센다.
            title=corrected_title(recording, stamp, glossary)[0],
            body=body,
        ),
        corrections=corrections,
    )


def render_lifelog_body(
    recording: LifelogRecording,
    *,
    extraction: ExtractionOutcome,
    tz: tzinfo = DEFAULT_TIMEZONE,
    glossary: term_correction.Glossary = (),
) -> str:
    """Render the v2 Markdown body (frontmatter first) for one recording."""
    return _render(recording, extraction, recording_stamp(recording, tz), glossary)[0]


def _corrected_fields(
    extraction: ExtractionOutcome, glossary: term_correction.Glossary
) -> tuple[ExtractionOutcome, tuple[term_correction.Correction, ...]]:
    """사람·장소·결정·할 일에만 교정을 건다 — 렌더된 본문 전체에 걸면 '## 전문' 까지 고쳐진다.

    시각(`at`)과 기한(`due`)은 낱말이 아니라 일정이라 손대지 않고, 생략 사유는 우리가 쓴
    문장이라 교정할 것이 없다.
    """
    if not isinstance(extraction, LifelogExtraction):
        return extraction, ()
    collected: list[term_correction.Correction] = []

    def fixed(text: str) -> str:
        repaired, corrections = term_correction.apply(text, glossary)
        collected.extend(corrections)
        return repaired

    corrected = LifelogExtraction(
        people=tuple(fixed(person) for person in extraction.people),
        places=tuple(fixed(place) for place in extraction.places),
        decisions=tuple(replace(item, text=fixed(item.text)) for item in extraction.decisions),
        todos=tuple(
            replace(todo, text=fixed(todo.text), owner=fixed(todo.owner))
            for todo in extraction.todos
        ),
    )
    return corrected, tuple(collected)


def _render(
    recording: LifelogRecording,
    extraction: ExtractionOutcome,
    stamp: datetime,
    glossary: term_correction.Glossary = (),
) -> tuple[str, tuple[term_correction.Correction, ...]]:
    summary, corrections = term_correction.apply(
        strip_unresolvable_images(recording.summary_markdown), glossary
    )
    title, title_corrections = corrected_title(recording, stamp, glossary)
    extraction, field_corrections = _corrected_fields(extraction, glossary)
    topics = topic_tags(summary)
    parts = [
        frontmatter(
            tags=(TAG_ROOT, *topics),
            title=title,
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
    return "\n\n".join(parts), (*title_corrections, *corrections, *field_corrections)


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
