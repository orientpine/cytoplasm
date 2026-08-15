"""Pick the next OPS_REFERENCE entry the node should propose for relocation.

Pure selection so the prod node drives reclamation itself instead of waiting for
someone to run the CLI.  Autonomy stops here: choosing a candidate is not an
external effect — the proposal still has to win cha's ✅ before anything is
written or deleted.

Conservative by construction: MEMORY.md only (USER.md holds identity/style and is
never relocatable in v1), never an entry a relocation record already covers, and
biggest-reclaim-first so each approval frees the most.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from automation.memory_curator.binding import entry_digest
from automation.memory_curator.classify_model import EntryVerdict
from automation.memory_curator.model import MemoryFile, MemoryKind
from automation.memory_curator.reclaim import reclaimable_chars

_RELOCATABLE_KIND: MemoryKind = "memory"
_RELOCATABLE_ROUTE = "OPS_REFERENCE"


@dataclass(frozen=True, slots=True)
class RelocationCandidate:
    source_kind: MemoryKind
    entry_text: str
    entry_sha256: str
    reclaimable_chars: int


def select_candidate(
    verdicts: Sequence[EntryVerdict],
    files: Mapping[str, MemoryFile],
    known_digests: frozenset[str],
) -> RelocationCandidate | None:
    """Return the biggest unhandled OPS_REFERENCE candidate, or None."""
    memory_file = files.get(_RELOCATABLE_KIND)
    if memory_file is None:
        return None

    best: RelocationCandidate | None = None
    claimed: set[int] = set()
    for verdict in verdicts:
        if verdict.route != _RELOCATABLE_ROUTE or verdict.source_kind != _RELOCATABLE_KIND:
            continue
        digest = entry_digest(_RELOCATABLE_KIND, verdict.entry_text)
        if digest in known_digests:
            continue
        index = _index_of(memory_file, verdict.entry_text, claimed)
        if index is None:
            continue
        claimed.add(index)
        candidate = RelocationCandidate(
            _RELOCATABLE_KIND,
            verdict.entry_text,
            digest,
            reclaimable_chars(memory_file, index),
        )
        if best is None or _outranks(candidate, best):
            best = candidate
    return best


def _index_of(memory_file: MemoryFile, text: str, claimed: set[int]) -> int | None:
    for index, entry in enumerate(memory_file.entries):
        if entry.text == text and index not in claimed:
            return index
    return None


def _outranks(candidate: RelocationCandidate, best: RelocationCandidate) -> bool:
    """Biggest reclaim wins; ties break on the digest so the choice is deterministic."""
    return (candidate.reclaimable_chars, best.entry_sha256) > (
        best.reclaimable_chars,
        candidate.entry_sha256,
    )
