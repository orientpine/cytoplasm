"""Owner-facing approval card for one plaud lifelog note push (render v5).

v2 (2026-09-02, owner request): the card quotes the first five sentence-sized
lines of the frozen note so the owner can tell what a recording contains before
pressing ✅. v1 carried only ids and hashes. v3 (2026-09-04, B안): the note now
opens with a '## 한눈에' block (녹음·주제·사람·장소·한 줄), so those lines are quoted
first and the summary fills the rest; the collapsed transcript loses its '> '.
The preview is presentation — the approval binding ('action_hash') is untouched,
so cards of any version bind the same push. Renderers are append-only versioned:
wording of an already-posted version is never edited in place.
v4 (2026-09-08): seven longer lines put the summary before glance metadata;
transcript-only previews disclose their source. The note approval heading and
separate approve / request changes / cancel lines make the review explicit.
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

RENDER_VERSION: Final = "plaud-sync-render-v5"
MAX_MESSAGE_CHARS: Final = 1900
# 1330 content characters + 21 quote/newline characters leave 549 for the card.
# Longer metadata still fails closed; the binding is never clipped to make room.
PREVIEW_LINES: Final = 7
PREVIEW_LINE_CHARS: Final = 190

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


def _clip(unit: str, limit: int) -> str:
    if len(unit) <= limit:
        return unit
    return unit[: limit - 1] + "…"


def summary_preview(body: str, *, render_version: str = RENDER_VERSION) -> str:
    """Summary first in v4; select v3 explicitly to reproduce its frozen preview.

    Never pad a summary/glance with transcript noise. A transcript-only fallback
    spends one of the seven lines on its source label, within the same budget.
    """
    if render_version == "plaud-sync-render-v3":
        return _summary_preview_v3(body)
    if render_version not in {"plaud-sync-render-v4", "plaud-sync-render-v5"}:
        raise PlaudRenderError(f"unsupported plaud render version: {render_version}")
    sections = lifelog_sections(body)
    units = _units(sections.get(SUMMARY_HEADING, "")) + _units(sections.get(GLANCE_HEADING, ""))
    if not units:
        units = _units(unquote_transcript(sections.get(TRANSCRIPT_HEADING, "")))
        if units:
            units.insert(0, "[전문 발췌 · 요약 없음]")
    return "\n".join(_clip(unit, PREVIEW_LINE_CHARS) for unit in units[:PREVIEW_LINES])


def _summary_preview_v3(body: str) -> str:
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
    return "\n".join(_clip(unit, 160) for unit in units[:5])


def render_plaud_approval(
    record: PlaudSyncRecord, *, preview: str = "", render_version: str = RENDER_VERSION,
) -> str:
    quoted = "\n".join(
        f"> {line}" for line in preview.splitlines() if line.strip()
    ) or "> (미리보기 없음)"
    match render_version:
        case "plaud-sync-render-v3":
            content = _render_v3(record, quoted)
        case "plaud-sync-render-v4":
            content = _render_v4(record, quoted)
        case "plaud-sync-render-v5":
            content = _render_v5(record, quoted)
        case _:
            raise PlaudRenderError(f"unsupported plaud render version: {render_version}")
    if len(content) > MAX_MESSAGE_CHARS:
        raise PlaudRenderError("plaud approval card exceeds the postable length")
    return content


def prepare_approval_card(record: PlaudSyncRecord, preview: str = "") -> tuple[str, str]:
    """Select once before effects; a stored version is replayed without fallback."""
    if record.render_version is not None:
        return record.render_version, render_plaud_approval(record, preview=preview, render_version=record.render_version)
    try:
        return RENDER_VERSION, render_plaud_approval(record, preview=preview)
    except PlaudRenderError:
        version = "plaud-sync-render-v4"
        return version, render_plaud_approval(record, preview=preview, render_version=version)


def _render_v5(record: PlaudSyncRecord, quoted: str) -> str:
    try:
        from automation.interop import owner_message as om
    except ImportError as error:
        raise PlaudRenderError("owner envelope unavailable") from error
    if not callable(getattr(om, "render", None)):
        raise PlaudRenderError("owner envelope renderer unavailable")
    here = om.Ref(scope="self")
    message = om.OwnerMessage(
        subject_key=record.recording_id, subject=f"PLAUD 노트: {record.note_title}",
        fact=f"{record.recorded_at}; {record.note_relpath}; plaud-sync-render-v5",
        location=here, owner=om.Action("react", here, "✅ 승인 / ⛔ 취소"),
        agent_next="Obsidian 저장·recall 인제스트; 수정은 이 스레드에 답글",
        recovery="not_applicable", detail=om.Approval(None, "저장 취소"),
    )
    try:
        lines = om.render(message, destination=here).splitlines()
        # Binding stays before the preview and decisions, outside the five human fields.
        return "\n".join((*lines[:3], f"- action_hash: `{record.action_hash}`", "", quoted, "", *lines[3:]))
    except om.OwnerMessageError as error:
        raise PlaudRenderError("owner envelope cannot render") from error


def _render_v3(record: PlaudSyncRecord, quoted: str) -> str:
    """Frozen v3 wording and limits, independent of the current defaults."""
    return (
        "[PLAUD lifelog 저장 승인 | plaud-sync-render-v3]\n"
        f"- 녹음 id: `{record.recording_id}`\n"
        f"- 녹음 시각: {record.recorded_at}\n"
        f"- 대상 노트: `{record.note_relpath}`\n"
        f"- action_hash: `{record.action_hash}`\n\n"
        f"내용 미리보기(상위 5줄):\n{quoted}\n\n"
        "승인(✅) 시 이 녹음의 요약+전문 노트가 Obsidian vault에 저장되고 "
        "recall 검색에 인제스트됩니다 — 취소는 ⛔."
    )


def _render_v4(record: PlaudSyncRecord, quoted: str) -> str:
    return (
        f"옵시디언 노트 승인 요청: **{record.note_title}**\n"
        "[PLAUD lifelog | plaud-sync-render-v4]\n"
        f"- 녹음 id: `{record.recording_id}`\n"
        f"- 녹음 시각: {record.recorded_at}\n"
        f"- 대상 노트: `{record.note_relpath}`\n"
        f"- action_hash: `{record.action_hash}`\n\n"
        f"내용 미리보기(요약 우선 · 최대 7줄):\n{quoted}\n\n"
        "승인(✅): 이 메시지에 반응 → 요약+전문을 Obsidian vault에 저장하고 recall 검색에 인제스트\n"
        "수정 요청: 승인 대신 이 스레드에 수정할 내용을 답글로 남겨 주세요\n"
        "취소(⛔): 이 메시지에 반응 → 저장 취소"
    )
