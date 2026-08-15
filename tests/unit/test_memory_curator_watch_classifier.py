from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation.memory_curator.binding import PromotionReceipt
from automation.memory_curator.classify_model import EntryVerdict
from automation.memory_curator.model import MemoryEntry, MemoryKind
from automation.memory_curator.promotion import PromotionProposal
from automation.memory_curator.state_store import load_state
from automation.memory_curator.watch import CycleResult, run_cycle

NOW: Final = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


class StubClassifier:
    def __init__(self, twin_texts: frozenset[str]) -> None:
        self.twin_texts: frozenset[str] = twin_texts

    def classify(
        self,
        entries_by_kind: Mapping[MemoryKind, tuple[MemoryEntry, ...]],
    ) -> tuple[EntryVerdict, ...]:
        return tuple(
            EntryVerdict(
                kind,
                entry.text,
                "TWIN" if entry.text in self.twin_texts else "UNCERTAIN",
                "",
                "",
                None,
                True,
            )
            for kind, entries in entries_by_kind.items()
            for entry in entries
        )


class RaisingClassifier:
    def classify(
        self,
        entries_by_kind: Mapping[MemoryKind, tuple[MemoryEntry, ...]],
    ) -> tuple[EntryVerdict, ...]:
        _ = entries_by_kind
        raise RuntimeError("classifier unavailable")


def _memory_dir(path: Path, memory: str, user: str) -> Path:
    path.mkdir()
    _ = (path / "MEMORY.md").write_text(memory, encoding="utf-8")
    _ = (path / "USER.md").write_text(user, encoding="utf-8")
    return path


def _promoter(calls: list[PromotionProposal]) -> Callable[[PromotionProposal], PromotionReceipt]:
    def promote(proposal: PromotionProposal) -> PromotionReceipt:
        calls.append(proposal)
        return PromotionReceipt(
            draft_id="draft-1",
            confirm_message_id="message-1",
            slug=proposal.slug,
            note_sha256="1" * 64,
        )

    return promote


def test_run_cycle_matches_baseline_when_classifier_is_none(tmp_path: Path) -> None:
    # Given: equivalent cue-matched native memory fixtures and deterministic effects.
    cue_entry = "앞으로 배려를 원칙으로 한다"
    memories = _memory_dir(tmp_path / "memories", cue_entry, "")
    baseline_state_path = tmp_path / "baseline-state.json"
    explicit_none_state_path = tmp_path / "explicit-none-state.json"
    baseline_calls: list[PromotionProposal] = []
    explicit_none_calls: list[PromotionProposal] = []

    # When: one cycle omits the optional classifier and one passes None explicitly.
    baseline = run_cycle(
        memories,
        baseline_state_path,
        promote=_promoter(baseline_calls),
        alert=lambda _text: True,
        read_twin=lambda _slug: None,
        now=NOW,
    )
    explicit_none = run_cycle(
        memories,
        explicit_none_state_path,
        promote=_promoter(explicit_none_calls),
        alert=lambda _text: True,
        read_twin=lambda _slug: None,
        now=NOW,
        classifier=None,
    )

    # Then: default-off injection is byte-for-byte behaviorally equivalent.
    assert explicit_none == baseline
    assert explicit_none.promoted == baseline.promoted
    assert load_state(explicit_none_state_path).promotions == load_state(baseline_state_path).promotions


def test_run_cycle_promotes_non_cue_entry_classified_as_twin(tmp_path: Path) -> None:
    # Given: a durable entry the literal cue matcher does not recognize.
    non_cue_entry = "다음 학기 연구실 사물함 번호는 42로 유지한다"
    memories = _memory_dir(tmp_path / "memories", non_cue_entry, "")
    calls: list[PromotionProposal] = []

    # When: an injected classifier routes that final native entry to TWIN.
    result = run_cycle(
        memories,
        tmp_path / "state.json",
        promote=_promoter(calls),
        alert=lambda _text: True,
        read_twin=lambda _slug: None,
        now=NOW,
        classifier=StubClassifier(frozenset({non_cue_entry})),
    )

    # Then: the existing proposal and post path promotes the formerly non-cue entry.
    assert [proposal.entry_text for proposal, _receipt in result.promoted] == [non_cue_entry]
    assert [proposal.entry_text for proposal in calls] == [non_cue_entry]


def test_run_cycle_deduplicates_twin_classification_of_cue_candidate(tmp_path: Path) -> None:
    # Given: one entry already selected by the literal cue matcher.
    cue_entry = "앞으로 배려를 원칙으로 한다"
    memories = _memory_dir(tmp_path / "memories", cue_entry, "")
    calls: list[PromotionProposal] = []

    # When: the classifier redundantly routes the same digest to TWIN.
    result = run_cycle(
        memories,
        tmp_path / "state.json",
        promote=_promoter(calls),
        alert=lambda _text: True,
        read_twin=lambda _slug: None,
        now=NOW,
        classifier=StubClassifier(frozenset({cue_entry})),
    )

    # Then: the entry is presented to the promotion path once.
    assert [proposal.entry_text for proposal, _receipt in result.promoted] == [cue_entry]
    assert [proposal.entry_text for proposal in calls] == [cue_entry]


def test_run_cycle_falls_back_to_cue_candidates_when_classifier_raises(tmp_path: Path) -> None:
    # Given: one cue candidate and a classifier whose invocation fails.
    cue_entry = "앞으로 배려를 원칙으로 한다"
    memories = _memory_dir(tmp_path / "memories", cue_entry, "")
    calls: list[PromotionProposal] = []

    # When: the optional classifier raises during the tick.
    result = run_cycle(
        memories,
        tmp_path / "state.json",
        promote=_promoter(calls),
        alert=lambda _text: True,
        read_twin=lambda _slug: None,
        now=NOW,
        classifier=RaisingClassifier(),
    )

    # Then: cue-only promotion succeeds and the tick returns normally.
    assert isinstance(result, CycleResult)
    assert [proposal.entry_text for proposal, _receipt in result.promoted] == [cue_entry]
    assert [proposal.entry_text for proposal in calls] == [cue_entry]
