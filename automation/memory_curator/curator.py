"""Pure curation logic over parsed Hermes memory files.

Split into three deterministic steps, each side-effect free:

1. ``parse_memory_file`` / ``serialize_memory_file`` — ``§``-delimited I/O
   with per-entry trimming and blank-entry dropping.
2. autonomous **lossless** compaction — exact-duplicate removal keyed on a
   whitespace/case-normalized form (no information is lost).
3. owner-gated **classification** — durable judgment is flagged as a twin
   promotion candidate; nothing is removed or promoted here.

Applying edits to real files, owner-approved promotion, and the cron
watcher live in separate modules; this one is host-testable and never
touches disk or network.
"""

from __future__ import annotations

import re

from .model import CAPS, CurationPlan, MemoryEntry, MemoryFile, MemoryKind

_SECTION_LINE = re.compile(r"^\s*§\s*$")
_JOIN = "\n§\n"

#: Curate raises the alert once a file crosses this fill ratio.
NEAR_CAP_RATIO = 0.85

#: Cues marking an entry as durable judgment (rule/principle/decision/
#: preference) that belongs in the decision twin, not the tiny native store.
_DURABLE_CUES: tuple[str, ...] = (
    "원칙",
    "규칙",
    "선호",
    "결정",
    "방침",
    "정책",
    "항상",
    "절대",
    "앞으로",
    "은혜",
    "배려",
    "하기로",
    "하는 것을",
    "우선한다",
    "지향한다",
)


def parse_memory_file(text: str, *, kind: MemoryKind) -> MemoryFile:
    if kind not in CAPS:
        raise ValueError(f"unknown memory kind: {kind!r}")
    entries: list[MemoryEntry] = []
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            entries.append(MemoryEntry(body))
        buffer.clear()

    for line in text.splitlines():
        if _SECTION_LINE.match(line):
            flush()
        else:
            buffer.append(line)
    flush()
    return MemoryFile(kind=kind, entries=tuple(entries))


def serialize_memory_file(memory_file: MemoryFile) -> str:
    return _JOIN.join(entry.text for entry in memory_file.entries)


def _normalize(text: str) -> str:
    """Whitespace-collapsed, case-folded key for lossless dedupe."""
    return " ".join(text.split()).casefold()


def _is_durable(text: str) -> bool:
    return any(cue in text for cue in _DURABLE_CUES)


def curate(memory_file: MemoryFile) -> CurationPlan:
    original_chars = memory_file.char_count

    seen: set[str] = set()
    kept: list[MemoryEntry] = []
    for entry in memory_file.entries:
        key = _normalize(entry.text)
        if key in seen:
            continue
        seen.add(key)
        kept.append(entry)

    compacted = MemoryFile(kind=memory_file.kind, entries=tuple(kept))
    promotion_candidates = tuple(e for e in compacted.entries if _is_durable(e.text))

    return CurationPlan(
        kind=memory_file.kind,
        original_chars=original_chars,
        compacted=compacted,
        compacted_chars=compacted.char_count,
        char_cap=memory_file.char_cap,
        promotion_candidates=promotion_candidates,
        near_cap=compacted.fill_ratio >= NEAR_CAP_RATIO,
    )
