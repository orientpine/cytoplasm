"""Autonomous candidate discovery for owner-gated relocation.

The node must find its own OPS_REFERENCE candidate — otherwise reclamation only
happens when a human runs the CLI. Selection is pure and conservative: biggest
reclaim first across both native stores, never an entry already handled.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.memory_curator.binding import entry_digest
from automation.memory_curator.classify_model import EntryVerdict, Route
from automation.memory_curator.model import MemoryEntry, MemoryFile, MemoryKind
from automation.memory_relocate.cron import memory_relocate_watch
from automation.memory_relocate.discover import select_candidate
from automation.memory_relocate.model import empty_state
from automation.rag_ingest.sensitivity import SensitivityRule
from automation.twin_distill.llm import LlmClient


def _verdict(kind: MemoryKind, text: str, route: Route) -> EntryVerdict:
    return EntryVerdict(
        source_kind=kind,
        entry_text=text,
        route=route,
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


def test_select_candidate_accepts_an_ops_reference_from_the_user_file() -> None:
    # Given: an OPS_REFERENCE verdict against USER.md.
    text = "y" * 120
    files = _files((), (text,))
    verdicts = [_verdict("user", text, "OPS_REFERENCE")]

    # When: candidate selection considers both native stores.
    picked = select_candidate(verdicts, files, frozenset())

    # Then: USER reaches the same source-qualified proposal boundary as MEMORY.
    assert picked is not None
    assert picked.source_kind == "user"
    assert picked.entry_sha256 == entry_digest("user", text)
    assert picked.reclaimable_chars > 0


def test_cron_discovery_when_user_is_ops_reference_reaches_proposed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the cron reads both native stores and its classifier selects one USER entry.
    text = "user-store operational reference"
    _ = (tmp_path / "MEMORY.md").write_text("", encoding="utf-8")
    _ = (tmp_path / "USER.md").write_text(text, encoding="utf-8")
    verdict = _verdict("user", text, "OPS_REFERENCE")

    def fake_classify(
        entries_by_kind: Mapping[MemoryKind, tuple[MemoryEntry, ...]],
        *,
        client: LlmClient,
        rules: Sequence[SensitivityRule],
    ) -> tuple[EntryVerdict, ...]:
        del entries_by_kind, client, rules
        return (verdict,)

    def fake_rules(_path: Path) -> tuple[SensitivityRule, ...]:
        return ()

    monkeypatch.setattr(memory_relocate_watch, "MEMORY_DIR", tmp_path)
    monkeypatch.setattr("automation.memory_curator.classify.classify_entries", fake_classify)
    monkeypatch.setattr("automation.rag_ingest.sensitivity.load_rules", fake_rules)
    monkeypatch.setenv("LITELLM_AGENT_KEY", "fixture-only")

    # When: the no-agent discovery seam runs without posting or deleting anything.
    proposed = memory_relocate_watch._discover_and_propose(
        empty_state(),
        datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
    )

    # Then: the persisted candidate is a genuine USER proposed record.
    assert len(proposed.relocations) == 1
    record = next(iter(proposed.relocations.values()))
    assert record.source_kind == "user"
    assert record.status == "proposed"
    assert record.entry_sha256 == entry_digest("user", text)


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
