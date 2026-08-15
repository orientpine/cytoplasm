"""Value objects for the Hermes native-memory curator.

Hermes stores ambient facts in two capped Markdown files, entries split by
lines that are just ``§``:

* ``MEMORY.md`` — environment / rules / lessons (cap 2200 chars);
* ``USER.md``   — name / role / preferences / style (cap 1375 chars).

These are pure, frozen value objects — no file or network access here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, TypeAlias

MemoryKind: TypeAlias = Literal["memory", "user"]

#: Hermes hard caps (chars) per file, source: Hermes docs 05-2 자체 메모리.
CAPS: Final[dict[MemoryKind, int]] = {"memory": 2200, "user": 1375}

#: Entries are serialized joined by a lone ``§`` line.
JOIN: Final = "\n§\n"


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    """A single ``§``-delimited fact/rule as written by Hermes."""

    text: str


@dataclass(frozen=True, slots=True)
class MemoryFile:
    """Parsed MEMORY.md / USER.md with cap-aware char accounting."""

    kind: MemoryKind
    entries: tuple[MemoryEntry, ...]

    @property
    def char_cap(self) -> int:
        return CAPS[self.kind]

    @property
    def char_count(self) -> int:
        return len(JOIN.join(entry.text for entry in self.entries))

    @property
    def fill_ratio(self) -> float:
        return self.char_count / self.char_cap


@dataclass(frozen=True, slots=True)
class CurationPlan:
    """Result of a single curation pass over one memory file.

    ``compacted`` holds the file after **autonomous, lossless** compaction
    (exact-duplicate removal + whitespace trim).  ``promotion_candidates``
    are durable-judgment entries only *flagged* for the decision twin — the
    curator never removes or promotes an entry on its own (owner-gated).
    """

    kind: MemoryKind
    original_chars: int
    compacted: MemoryFile
    compacted_chars: int
    char_cap: int
    promotion_candidates: tuple[MemoryEntry, ...]
    near_cap: bool

    @property
    def headroom_after(self) -> int:
        return self.char_cap - self.compacted_chars

    @property
    def freed_chars(self) -> int:
        return self.original_chars - self.compacted_chars
