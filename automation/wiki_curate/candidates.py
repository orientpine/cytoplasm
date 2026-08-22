"""Pure candidate selection: which Obsidian notes deserve a wiki draft."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TypeAlias

Clock: TypeAlias = Callable[[], datetime]

REVIEW_AFTER_DAYS = 180
"""Every proposal must carry a review date; this is the single place it comes from."""

_SENSITIVE = "patent-sensitive"


@dataclass(frozen=True, slots=True)
class SourceNote:
    ref: str
    title: str
    body: str
    tags: tuple[str, ...]
    sensitivity: str | None
    event_date: str | None
    entities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Candidate:
    source_ref: str
    title: str
    body: str
    tags: tuple[str, ...]
    digest: str
    entity: tuple[str, ...]
    relations: tuple[str, ...]
    event_date: str | None
    review_after: str


def content_digest(body: str) -> str:
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


def _event_ordinal(event_date: str | None) -> int:
    if not event_date:
        return 0
    try:
        return -date.fromisoformat(event_date).toordinal()
    except ValueError:
        return 0


def _order(note: SourceNote) -> tuple[int, int, str]:
    return (0 if note.event_date else 1, _event_ordinal(note.event_date), note.ref)


def select_candidates(
    notes: Iterable[SourceNote],
    *,
    existing_digests: frozenset[str],
    existing_origins: frozenset[str] = frozenset(),
    limit: int,
    clock: Clock,
) -> tuple[Candidate, ...]:
    """Rank distillable notes, newest event first, skipping what must not be proposed.

    Two skips guard against re-proposing the same source. ``existing_digests``
    catches a verbatim copy; ``existing_origins`` catches the normal case, where
    the stored note is a *distillation* and therefore never matches the source
    body byte-for-byte.
    """
    review_after = (clock().date() + timedelta(days=REVIEW_AFTER_DAYS)).isoformat()
    picked: list[Candidate] = []
    for note in sorted(notes, key=_order):
        if len(picked) >= limit:
            break
        if note.sensitivity == _SENSITIVE:
            continue
        if note.ref in existing_origins:
            continue
        body = note.body.strip()
        if not body:
            continue
        digest = content_digest(body)
        if digest in existing_digests:
            continue
        picked.append(
            Candidate(
                source_ref=note.ref,
                title=note.title,
                body=body,
                tags=tuple(note.tags),
                digest=digest,
                entity=tuple(note.entities),
                relations=(f"source:{note.ref}",),
                event_date=note.event_date,
                review_after=review_after,
            )
        )
    return tuple(picked)
