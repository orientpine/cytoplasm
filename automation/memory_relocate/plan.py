"""Deterministic Obsidian destination planning for a relocated operational fact.

Pure planning only: no I/O, no clock, no write.  ``render_note`` stamps the
Created/Modified dates at write time, so the owner-approval hash binds the
*plan* (path, title, body) and stays stable across approval and application.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from automation.obsidian_write.config import ObsidianWriteError
from automation.obsidian_write.note import NotePlan, plan_note

_TITLE_SUMMARY_LIMIT: Final = 60
_TITLE_DIGEST_LENGTH: Final = 8
_ENTRY_HEADING: Final = "## 원본 기록 (verbatim)"
_PROVENANCE_FOOTER: Final = (
    "---\n"
    "provenance: observed\n"
    "authority: reference\n"
    "네이티브 메모리(native memory)에서 이관된 관측 기반 운영 사실 참조입니다. "
    "조회(recall)용 reference일 뿐 권위 있는 설정이 아닙니다 (NOT authoritative config)."
)


@dataclass(frozen=True, slots=True)
class RelocationPlan:
    """One relocated entry's planned Obsidian note and its approval-binding hash."""

    note_plan: NotePlan
    note_plan_sha256: str


def build_relocation_plan(entry_text: str) -> RelocationPlan:
    """Plan the ops-reference note for one native-memory entry, byte-preserving."""
    title = _entry_title(entry_text)
    body = _entry_body(entry_text)
    note_plan = plan_note(title, body, institutional=False, bucket_hint="resource")
    return RelocationPlan(note_plan, _note_plan_sha256(note_plan))


def _entry_title(entry_text: str) -> str:
    """Derive a short title, digest-suffixed so distinct entries never share a path."""
    digest = hashlib.sha256(entry_text.encode("utf-8")).hexdigest()[:_TITLE_DIGEST_LENGTH]
    return f"{_summary_line(entry_text)} ({digest})"


def _summary_line(entry_text: str) -> str:
    for line in entry_text.splitlines():
        collapsed = " ".join(line.split())
        if collapsed:
            return collapsed[:_TITLE_SUMMARY_LIMIT].strip()
    raise ObsidianWriteError("Relocated memory entry has no titleable content", False)


def _entry_body(entry_text: str) -> str:
    """Keep the entry verbatim between a heading and the footer so no strip touches it."""
    return f"{_ENTRY_HEADING}\n\n{entry_text}\n\n{_PROVENANCE_FOOTER}"


def _note_plan_sha256(note_plan: NotePlan) -> str:
    preimage = f"{note_plan.relpath.as_posix()}\0{note_plan.title}\0{note_plan.body}"
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()
