"""Reclaim accounting that ranks promotion candidates by freed chars.

The curator promotes at most three entries per tick, so a tick that spends its
slots on short entries leaves a near-cap file near cap.  This module answers
"how many chars does removing this entry actually free?" using the same ``§``
join accounting the deletion path uses, and orders candidates biggest-first.

Pure and stdlib-only — no file, network, or clock access here.
"""

from __future__ import annotations

from collections.abc import Mapping

from .binding import entry_digest
from .model import MemoryEntry, MemoryFile, MemoryKind

#: Ranking key: reclaim descending, then kind and digest ascending.
_SortKey = tuple[int, str, str]
_Ranked = tuple[MemoryKind, int, MemoryEntry]


def reclaimable_chars(memory_file: MemoryFile, index: int) -> int:
    """Return the exact ``char_count`` delta of removing entry ``index``.

    This mirrors the curator's deletion accounting: the file's current
    ``char_count`` minus the ``char_count`` of the same file with that one
    entry removed, so the ``§`` separator that disappears with it is counted.
    """
    entries = memory_file.entries
    if not 0 <= index < len(entries):
        message = f"memory entry index is out of range: {index}"
        raise IndexError(message)
    remaining = entries[:index] + entries[index + 1 :]
    return memory_file.char_count - MemoryFile(memory_file.kind, remaining).char_count


def order_by_reclaim(
    candidates: Mapping[MemoryKind, tuple[MemoryEntry, ...]],
    files: Mapping[MemoryKind, MemoryFile],
) -> tuple[_Ranked, ...]:
    """Bind each candidate to its file index and rank by freed chars, biggest first.

    Ties break on ascending ``(kind, entry_digest)`` so a tick that runs twice
    over the same inputs spends its promotion slots on the same entries.  A
    candidate whose text is no longer in its file fails closed with
    ``ValueError`` rather than being silently dropped from the tick.
    """
    decorated: list[tuple[_SortKey, _Ranked]] = []
    for kind, kind_candidates in candidates.items():
        memory_file = files[kind]
        claimed: set[int] = set()
        for entry in kind_candidates:
            index = _first_unclaimed_index(memory_file, entry.text, claimed)
            claimed.add(index)
            key: _SortKey = (
                -reclaimable_chars(memory_file, index),
                kind,
                entry_digest(kind, entry.text),
            )
            decorated.append((key, (kind, index, entry)))
    decorated.sort(key=lambda item: item[0])
    return tuple(ranked for _, ranked in decorated)


def _first_unclaimed_index(memory_file: MemoryFile, text: str, claimed: set[int]) -> int:
    """Bind one candidate text to the earliest matching index not already taken."""
    for index, entry in enumerate(memory_file.entries):
        if entry.text == text and index not in claimed:
            return index
    message = f"promotion candidate is not present in the {memory_file.kind} memory file"
    raise ValueError(message)
