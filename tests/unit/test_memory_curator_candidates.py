from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from automation.memory_curator.binding import PromotionReceipt
from automation.memory_curator.promotion import PromotionProposal, build_proposal, content_hash
from automation.memory_curator.state import CuratorState, PromotionRecord, empty_state
from automation.memory_curator.state_store import load_state, save_state
from automation.memory_curator.watch import CycleResult, run_cycle

_NOW = datetime(2026, 7, 31, tzinfo=UTC)


def _memory_dir(tmp_path: Path, *, memory: str, user: str) -> Path:
    memories = tmp_path / "memories"
    memories.mkdir()
    _ = (memories / "MEMORY.md").write_text(memory, encoding="utf-8")
    _ = (memories / "USER.md").write_text(user, encoding="utf-8")
    return memories


def _run(
    memories: Path,
    state_path: Path,
    *,
    max_promotions: int = 3,
) -> CycleResult:
    receipt_count = 0

    def promote(proposal: PromotionProposal) -> PromotionReceipt:
        nonlocal receipt_count
        receipt_count += 1
        return PromotionReceipt(
            draft_id=f"draft-{receipt_count}",
            confirm_message_id=f"message-{receipt_count}",
            slug=proposal.slug,
            note_sha256=str(receipt_count) * 64,
        )

    return run_cycle(
        memories,
        state_path,
        promote=promote,
        alert=lambda _message: True,
        read_twin=lambda _slug: None,
        now=_NOW,
        max_promotions=max_promotions,
    )


def _legacy_record(entry_text: str) -> PromotionRecord:
    proposal = build_proposal(entry_text, source_kind="user")
    return PromotionRecord(
        source_kind="user",
        entry_sha256=proposal.entry_digest,
        slug=proposal.slug,
        created_at="2026-07-30T00:00:00Z",
        note_sha256="",
        draft_id=None,
        confirm_message_id=None,
        status="legacy_unbound",
        posted_at=None,
        reconciled_at=None,
        backup_path=None,
        last_block_reason=None,
    )


def _promoted_entries(result: CycleResult) -> tuple[tuple[str, str], ...]:
    return tuple((proposal.source_kind, proposal.entry_text) for proposal, _ in result.promoted)


def test_run_cycle_characterizes_file_order_for_durable_memory_entries(tmp_path: Path) -> None:
    # Given: several cue-matched durable entries in their native file order.
    tiny = "앞으로 A를 원칙으로 한다"
    medium = "앞으로 B를 원칙으로 하며 동료를 배려한다"
    largest = "앞으로 C를 원칙으로 하며 장기적인 협업과 투명한 의사결정을 우선한다"
    memories = _memory_dir(
        tmp_path,
        memory="\n§\n".join((tiny, medium, largest)),
        user="환경 사실",
    )
    state_path = tmp_path / "state.json"

    # When: today's watcher processes the file under a three-promotion cap.
    result = _run(memories, state_path)
    promotions = load_state(state_path).promotions

    # Then: the largest reclaim is promoted first and every receipt-bound state is recorded.
    assert _promoted_entries(result) == (("memory", largest), ("memory", medium), ("memory", tiny))
    assert {record.status for record in promotions.values()} == {"posted"}
    assert len(promotions) == 3


def test_run_cycle_characterizes_legacy_unbound_candidate_skip(tmp_path: Path) -> None:
    # Given: one legacy-unbound durable entry followed by a new durable entry.
    legacy = "앞으로 기존 약속을 원칙으로 한다"
    fresh = "앞으로 새 협업 규칙을 원칙으로 한다"
    memories = _memory_dir(tmp_path, memory="환경 사실", user=f"{legacy}\n§\n{fresh}")
    state_path = tmp_path / "state.json"
    state = empty_state()
    save_state(
        state_path,
        CuratorState(
            state.version,
            {content_hash(legacy): _legacy_record(legacy)},
            state.alert,
            state.pending_owner_events,
        ),
    )

    # When: today's watcher scans the mixed legacy/current candidates.
    result = _run(memories, state_path)
    promotions = load_state(state_path).promotions

    # Then: legacy coverage prevents a duplicate post while the fresh entry is posted.
    assert _promoted_entries(result) == (("user", fresh),)
    assert promotions[content_hash(legacy)].status == "legacy_unbound"
    assert {record.status for record in promotions.values()} == {"legacy_unbound", "posted"}


def test_run_cycle_characterizes_memory_before_user_file_order(tmp_path: Path) -> None:
    # Given: a short memory candidate and a larger user candidate.
    memory_entry = "앞으로 작은 기준을 원칙으로 한다"
    user_entry = "앞으로 더 긴 사용자 협업 기준을 원칙으로 하며 중요한 결정을 투명하게 공유한다"
    memories = _memory_dir(
        tmp_path,
        memory=memory_entry,
        user=f"이름 <owner-name>\n§\n{user_entry}",
    )
    state_path = tmp_path / "state.json"

    # When: today's watcher processes both native files.
    result = _run(memories, state_path)
    promotions = load_state(state_path).promotions

    # Then: the larger USER.md candidate outranks the smaller MEMORY.md candidate.
    assert _promoted_entries(result) == (("user", user_entry), ("memory", memory_entry))
    assert {record.status for record in promotions.values()} == {"posted"}
    assert len(promotions) == 2


def test_run_cycle_promotes_three_largest_nonlegacy_candidates(tmp_path: Path) -> None:
    # Given: five cue-matched candidates of different sizes, including a legacy-unbound one.
    smallest = "앞으로 S를 원칙으로 한다"
    medium = "앞으로 M을 원칙으로 하며 동료를 배려한다"
    legacy = "앞으로 L을 원칙으로 하며 가장 긴 기존 약속을 계속 지킨다"
    large = "앞으로 G를 원칙으로 하며 장기 협업의 투명한 의사결정을 우선한다"
    largest = "앞으로 X를 원칙으로 하며 장기 협업에서 투명한 의사결정과 상호 신뢰를 가장 먼저 우선한다"
    memories = _memory_dir(
        tmp_path,
        memory="환경 사실",
        user="\n§\n".join((smallest, medium, legacy, large, largest)),
    )
    state_path = tmp_path / "state.json"
    state = empty_state()
    save_state(
        state_path,
        CuratorState(
            state.version,
            {content_hash(legacy): _legacy_record(legacy)},
            state.alert,
            state.pending_owner_events,
        ),
    )

    # When: the watcher applies a three-promotion cap.
    result = _run(memories, state_path, max_promotions=3)
    promotions = load_state(state_path).promotions

    # Then: it spends every slot on the three largest eligible candidates.
    assert _promoted_entries(result) == (("user", largest), ("user", large), ("user", medium))
    assert promotions[content_hash(legacy)].status == "legacy_unbound"
    assert {record.status for record in promotions.values()} == {"legacy_unbound", "posted"}
    assert len(promotions) == 4
