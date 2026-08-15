"""Autonomous candidate discovery for owner-gated relocation.

The node must find its own OPS_REFERENCE candidate — otherwise reclamation only
happens when a human runs the CLI. Selection is pure and conservative: biggest
reclaim first, MEMORY.md only, never an entry already handled.
"""

from __future__ import annotations

from automation.memory_curator.binding import entry_digest
from automation.memory_curator.classify_model import EntryVerdict
from automation.memory_curator.model import MemoryEntry, MemoryFile
from automation.memory_relocate.discover import select_candidate


def _verdict(kind: str, text: str, route: str) -> EntryVerdict:
    return EntryVerdict(
        source_kind=kind,  # type: ignore[arg-type]
        entry_text=text,
        route=route,  # type: ignore[arg-type]
        evidence="",
        reason="",
        veto=None,
        llm_called=True,
    )


def _files(memory: tuple[str, ...], user: tuple[str, ...] = ()) -> dict[str, MemoryFile]:
    return {
        "memory": MemoryFile("memory", tuple(MemoryEntry(t) for t in memory)),
        "user": MemoryFile("user", tuple(MemoryEntry(t) for t in user)),
    }


def test_select_candidate_picks_the_biggest_ops_reference_entry() -> None:
    # Given: two OPS_REFERENCE facts of different size plus a keep-native one.
    small, big, keep = "포트 4000 사실", "x" * 200, "이름은 <owner-name>"
    files = _files((small, big, keep))
    verdicts = [
        _verdict("memory", small, "OPS_REFERENCE"),
        _verdict("memory", big, "OPS_REFERENCE"),
        _verdict("memory", keep, "KEEP_NATIVE"),
    ]

    # When: the node picks what to propose next.
    picked = select_candidate(verdicts, files, frozenset())

    # Then: the biggest reclaim wins, so each owner ✅ frees the most.
    assert picked is not None
    assert picked.entry_text == big
    assert picked.reclaimable_chars > 0


def test_select_candidate_never_proposes_the_user_file() -> None:
    # Given: an OPS_REFERENCE verdict against USER.md (identity/style — never relocatable in v1).
    text = "y" * 120
    files = _files((), (text,))
    verdicts = [_verdict("user", text, "OPS_REFERENCE")]

    # When / Then: it is refused outright.
    assert select_candidate(verdicts, files, frozenset()) is None


def test_select_candidate_skips_entries_already_handled() -> None:
    # Given: the only OPS_REFERENCE entry is already tracked by a relocation record.
    text = "z" * 150
    files = _files((text,))
    verdicts = [_verdict("memory", text, "OPS_REFERENCE")]
    known = frozenset({entry_digest("memory", text)})

    # When / Then: no duplicate proposal is produced.
    assert select_candidate(verdicts, files, known) is None


def test_select_candidate_ignores_non_ops_routes() -> None:
    # Given: durable judgment and uncertain entries only.
    files = _files(("a" * 100, "b" * 100))
    verdicts = [
        _verdict("memory", "a" * 100, "TWIN"),
        _verdict("memory", "b" * 100, "UNCERTAIN"),
    ]

    # When / Then: relocation only ever claims OPS_REFERENCE facts.
    assert select_candidate(verdicts, files, frozenset()) is None
