"""Integration contract for one memory-curator watch cycle."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from automation.memory_curator.binding import PromotionReceipt
from automation.memory_curator.promotion import PromotionProposal
from automation.memory_curator.state_store import load_state
from automation.memory_curator.watch import CycleResult, run_cycle


def _memory_dir(tmp_path: Path, *, user: str, memory: str = "") -> Path:
    memories = tmp_path / "memories"
    memories.mkdir()
    _ = (memories / "USER.md").write_text(user, encoding="utf-8")
    _ = (memories / "MEMORY.md").write_text(memory, encoding="utf-8")
    return memories


def _receipt(proposal: PromotionProposal, suffix: str = "1") -> PromotionReceipt:
    return PromotionReceipt(
        draft_id=f"draft-{suffix}",
        confirm_message_id=f"message-{suffix}",
        slug=proposal.slug,
        note_sha256=suffix * 64,
    )


def _run(
    memories: Path,
    state_path: Path,
    *,
    promote: Callable[[PromotionProposal], PromotionReceipt | None],
    alert: Callable[[str], bool] = lambda _message: True,
    max_promotions: int = 3,
) -> CycleResult:
    return run_cycle(
        memories,
        state_path,
        promote=promote,
        alert=alert,
        read_twin=lambda _slug: None,
        max_promotions=max_promotions,
    )


def test_run_cycle_compacts_then_posts_a_durable_candidate(tmp_path: Path) -> None:
    # Given: a duplicate fact and one durable judgment in native memory.
    memories = _memory_dir(
        tmp_path,
        user="이름 <owner-name>\n§\n앞으로 배려를 원칙으로 한다\n§\n이름 <owner-name>",
        memory="환경 사실 하나",
    )
    state_path = tmp_path / "state.json"
    calls: list[PromotionProposal] = []

    # When: one live cycle compacts and posts through the injected gate effect.
    result = _run(
        memories,
        state_path,
        promote=lambda proposal: calls.append(proposal) or _receipt(proposal),
    )

    # Then: compaction precedes the posted, receipt-bound state transition.
    assert isinstance(result, CycleResult)
    assert (memories / "USER.md").read_text(encoding="utf-8").count("이름 <owner-name>") == 1
    assert len(result.promoted) == len(calls) == 1
    proposal, receipt = result.promoted[0]
    record = load_state(state_path).promotions[proposal.promotion_key]
    assert receipt == _receipt(proposal)
    assert record.status == "posted"
    assert record.draft_id == receipt.draft_id
    assert record.note_sha256 == receipt.note_sha256


def test_run_cycle_does_not_repost_a_posted_candidate(tmp_path: Path) -> None:
    # Given: one durable candidate and a successful first-tick poster.
    memories = _memory_dir(tmp_path, user="앞으로 배려를 원칙으로 한다")
    state_path = tmp_path / "state.json"
    calls: list[PromotionProposal] = []

    def promote(proposal: PromotionProposal) -> PromotionReceipt:
        calls.append(proposal)
        return _receipt(proposal)

    # When: the unchanged state is processed twice.
    _ = _run(memories, state_path, promote=promote)
    _ = _run(memories, state_path, promote=promote)

    # Then: the posted key is not proposed again.
    assert len(calls) == 1
    assert load_state(state_path).promotions[calls[0].promotion_key].status == "posted"


def test_run_cycle_keeps_failed_post_prepared_and_retries_next_tick(
    tmp_path: Path,
) -> None:
    # Given: one durable candidate whose first post attempt fails.
    memories = _memory_dir(tmp_path, user="앞으로 배려를 원칙으로 한다")
    state_path = tmp_path / "state.json"
    attempts: list[PromotionProposal] = []

    def fail(proposal: PromotionProposal) -> None:
        attempts.append(proposal)
        return None

    # When: a failed tick is followed by a successful retry tick.
    first = _run(memories, state_path, promote=fail)
    prepared = load_state(state_path).promotions[attempts[0].promotion_key]
    second = _run(
        memories,
        state_path,
        promote=lambda proposal: attempts.append(proposal) or _receipt(proposal),
    )

    # Then: the claim survived as prepared and the same key became posted on retry.
    assert first.promoted == ()
    assert prepared.status == "prepared"
    assert prepared.last_block_reason == "post_failed"
    assert len(attempts) == 2
    assert attempts[0].promotion_key == attempts[1].promotion_key
    assert len(second.promoted) == 1
    assert load_state(state_path).promotions[attempts[0].promotion_key].status == "posted"


def test_run_cycle_caps_total_post_attempts_per_tick(tmp_path: Path) -> None:
    # Given: four durable candidates and a two-attempt tick cap.
    entries = "\n§\n".join(f"앞으로 {letter}를 원칙으로 한다" for letter in "ABCD")
    memories = _memory_dir(tmp_path, user=entries)
    state_path = tmp_path / "state.json"
    calls: list[PromotionProposal] = []

    def promote(proposal: PromotionProposal) -> PromotionReceipt:
        calls.append(proposal)
        return _receipt(proposal, str(len(calls)))

    # When: two consecutive cycles process the backlog.
    _ = _run(memories, state_path, promote=promote, max_promotions=2)
    assert len(calls) == 2
    _ = _run(memories, state_path, promote=promote, max_promotions=2)

    # Then: each tick posts at most two and all four become posted exactly once.
    assert len(calls) == 4
    assert len({proposal.promotion_key for proposal in calls}) == 4


def test_run_cycle_reports_a_bounded_promotion_preview(tmp_path: Path) -> None:
    # Given: a durable entry longer than the owner-DM preview limit.
    entry = "앞으로 호의를 베풀 상대를 배려하는 것을 원칙으로 한다 그리고 본문은 더 길다"
    memories = _memory_dir(tmp_path, user=entry)
    reports: list[str] = []

    # When: the first actionable tick posts and sends its change-detected report.
    result = _run(
        memories,
        tmp_path / "state.json",
        promote=lambda proposal: _receipt(proposal),
        alert=lambda text: reports.append(text) or True,
    )

    # Then: one report identifies the draft and kind without exposing the full body.
    assert result.alerted is True
    assert len(reports) == 1
    assert reports[0].startswith("🧠 메모리 큐레이터")
    assert "저장 draft-1" in reports[0]
    assert "principle" in reports[0]
    assert entry not in reports[0]
