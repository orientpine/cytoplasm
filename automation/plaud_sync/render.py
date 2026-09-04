"""Owner-facing approval card for one plaud lifelog note push (render v3).

v2 (2026-09-02, owner request): the card quotes the first five sentence-sized
lines of the frozen note so the owner can tell what a recording contains before
pressing ✅. v1 carried only ids and hashes. v3 (2026-09-04, B안): the note now
opens with a '## 한눈에' block (녹음·주제·사람·장소·한 줄), so those lines are quoted
first and the summary fills the rest; the collapsed transcript loses its '> '.
The preview is presentation — the approval binding ('action_hash') is untouched,
so cards of any version bind the same push. Renderers are append-only versioned:
wording of an already-posted version is never edited in place.
"""

from __future__ import annotations

import re
from typing import Final

from .lifelog_fields import (
    GLANCE_HEADING,
    SUMMARY_HEADING,
    TRANSCRIPT_HEADING,
    lifelog_sections,
    unquote_transcript,
)
from .model import PlaudSyncRecord

RENDER_VERSION: Final = "plaud-sync-render-v3"
MAX_MESSAGE_CHARS: Final = 1900
PREVIEW_LINES: Final = 5
PREVIEW_LINE_CHARS: Final = 160

_PLACEHOLDER: Final = re.compile(r"^-?\s*\((요약|전사|전문) 없음\)$")
_HEADING: Final = re.compile(r"^#{1,6}\s+")
_RULE: Final = re.compile(r"^(-{3,}|_{3,}|\*{3,})$")
_IMAGE: Final = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_SENTENCE_BREAK: Final = re.compile(r"(?<=[.!?。])\s+")


class PlaudRenderError(ValueError):
    """The approval card cannot be posted without truncating its binding."""


def _units(section: str) -> list[str]:
    units: list[str] = []
    for raw in section.splitlines():
        line = _IMAGE.sub("", _HEADING.sub("", raw.strip())).strip()
        if not line or _PLACEHOLDER.match(line) or _RULE.match(line):
            continue
        units.extend(part for part in _SENTENCE_BREAK.split(line) if part)
    return units


def _clip(unit: str) -> str:
    if len(unit) <= PREVIEW_LINE_CHARS:
        return unit
    return unit[: PREVIEW_LINE_CHARS - 1] + "…"


def summary_preview(body: str) -> str:
    """The first five sentence-sized lines: 한눈에 first, then 요약, else the transcript.

    Headings lose their '#' so they do not render as banners inside a blockquote;
    placeholders ('- (요약 없음)') count as absent so an empty summary falls back to
    the transcript instead of previewing the placeholder itself. Frontmatter and the
    결정 · 할 일 section are never quoted — the card is a glance, not the note.
    """
    sections = lifelog_sections(body)
    units = _units(sections.get(GLANCE_HEADING, "")) + _units(sections.get(SUMMARY_HEADING, ""))
    if not units:
        units = _units(unquote_transcript(sections.get(TRANSCRIPT_HEADING, "")))
    return "\n".join(_clip(unit) for unit in units[:PREVIEW_LINES])


def render_plaud_approval(record: PlaudSyncRecord, *, preview: str = "") -> str:
    quoted = "\n".join(
        f"> {line}" for line in preview.splitlines() if line.strip()
    ) or "> (미리보기 없음)"
    content = (
        f"[PLAUD lifelog 저장 승인 | {RENDER_VERSION}]\n"
        f"- 녹음 id: `{record.recording_id}`\n"
        f"- 녹음 시각: {record.recorded_at}\n"
        f"- 대상 노트: `{record.note_relpath}`\n"
        f"- action_hash: `{record.action_hash}`\n\n"
        f"내용 미리보기(상위 {PREVIEW_LINES}줄):\n{quoted}\n\n"
        "승인(✅) 시 이 녹음의 요약+전문 노트가 Obsidian vault에 저장되고 "
        "recall 검색에 인제스트됩니다 — 취소는 ⛔."
    )
    if len(content) > MAX_MESSAGE_CHARS:
        raise PlaudRenderError("plaud approval card exceeds the postable length")
    return content
