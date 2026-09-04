"""Shapes shared by lifelog fetch, LLM field extraction and note rendering (no I/O)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True, slots=True)
class LifelogRecording:
    """The PLAUD recording data needed to build one vault note."""

    id: str
    name: str
    created_at: str
    start_at: str
    duration_ms: int
    summary_markdown: str
    transcript_text: str
    transcript_source: str = ""


@dataclass(frozen=True, slots=True)
class LifelogDecision:
    """One thing the conversation settled; 'at' is the transcript stamp (MM:SS) or ''."""

    text: str
    at: str = ""


@dataclass(frozen=True, slots=True)
class LifelogTodo:
    """One follow-up; owner/due are '' when nobody said them, 'at' as above."""

    text: str
    owner: str = ""
    due: str = ""
    at: str = ""


@dataclass(frozen=True, slots=True)
class LifelogExtraction:
    """LLM-extracted structured fields for the note's 한눈에 and 결정 · 할 일 sections."""

    people: tuple[str, ...] = ()
    places: tuple[str, ...] = ()
    decisions: tuple[LifelogDecision, ...] = ()
    todos: tuple[LifelogTodo, ...] = ()


@dataclass(frozen=True, slots=True)
class ExtractionSkipped:
    """Extraction was deliberately not attempted; 'reason' is owner-facing Korean.

    The note is still frozen — with the reason on its 한눈에 line — because the
    skip is permanent for this recording (sensitivity gate, no LLM configured).
    A *transient* failure is not a skip: it raises LifelogExtractError and the
    recording waits for the next poll instead of freezing a degraded note.
    """

    reason: str


ExtractionOutcome: TypeAlias = LifelogExtraction | ExtractionSkipped
Extractor: TypeAlias = Callable[[LifelogRecording], ExtractionOutcome]


class LifelogExtractError(RuntimeError):
    """Extraction could not complete this poll (transport/parse); retry next poll."""
