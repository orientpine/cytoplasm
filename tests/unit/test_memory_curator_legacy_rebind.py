"""Owner-driven reclamation of pre-v2 ``legacy_unbound`` promotions.

The v1->v2 migration parks old proposed hashes as ``legacy_unbound`` records
that reconcile refuses to delete (they were approved under the old confirm
text, before the deletion-explaining marker existed).  ``rebind_legacy`` drops
up to ``max_rebind`` legacy records whose entry is STILL present, so the normal
cycle re-proposes each as a fresh marker-bound promotion for cha's owner-DM
approval.  It deletes NOTHING itself.
"""

from __future__ import annotations

from automation.memory_curator.legacy_rebind import rebind_legacy
from automation.memory_curator.promotion import content_hash
from automation.memory_curator.state import (
    AlertState,
    CuratorState,
    PromotionRecord,
)


def _legacy(entry_text: str) -> PromotionRecord:
    # Given: a migrated legacy record keyed on the old whitespace-normalized hash.
    digest = content_hash(entry_text)
    return PromotionRecord(
        source_kind="memory",
        entry_sha256=digest,
        slug="",
        created_at="",
        note_sha256="",
        draft_id=None,
        confirm_message_id=None,
        status="legacy_unbound",
        posted_at=None,
        reconciled_at=None,
        backup_path=None,
        last_block_reason=None,
    )


def _state(*entries: str) -> CuratorState:
    promotions = {content_hash(text): _legacy(text) for text in entries}
    return CuratorState(3, promotions, AlertState(None, None, None, None), {})


def test_rebind_drops_present_legacy_records_up_to_the_cap() -> None:
    # Given: three legacy records, all still present in native memory.
    present = frozenset(content_hash(text) for text in ("가", "나", "다"))
    state = _state("가", "나", "다")

    # When: reclamation runs with a cap of two.
    new_state, dropped = rebind_legacy(state, present, max_rebind=2)

    # Then: exactly two legacy records are dropped so the cycle re-proposes them.
    assert len(dropped) == 2
    assert len(new_state.promotions) == 1
    assert all(new_state.promotions[key].status == "legacy_unbound" for key in new_state.promotions)


def test_rebind_never_drops_an_absent_legacy_record() -> None:
    # Given: a legacy record whose entry is no longer in native memory.
    state = _state("사라진 항목")

    # When: reclamation runs but nothing matches the present set.
    new_state, dropped = rebind_legacy(state, frozenset(), max_rebind=3)

    # Then: the absent record is preserved untouched (no phantom reclamation).
    assert dropped == ()
    assert len(new_state.promotions) == 1


def test_rebind_leaves_non_legacy_records_alone() -> None:
    # Given: a posted (non-legacy) record whose entry is present.
    text = "게시된 항목"
    posted = PromotionRecord(
        source_kind="memory",
        entry_sha256=content_hash(text),
        slug="memory-promoted-memory-abc",
        created_at="2026-07-30T00:00:00Z",
        note_sha256="c" * 64,
        draft_id="d1",
        confirm_message_id="m1",
        status="posted",
        posted_at="2026-07-30T00:00:00Z",
        reconciled_at=None,
        backup_path=None,
        last_block_reason=None,
    )
    state = CuratorState(
        3,
        {content_hash(text): posted},
        AlertState(None, None, None, None),
        {},
    )

    # When: reclamation runs over a present posted record.
    new_state, dropped = rebind_legacy(state, frozenset({content_hash(text)}), max_rebind=3)

    # Then: only legacy_unbound records are ever dropped.
    assert dropped == ()
    assert new_state.promotions[content_hash(text)].status == "posted"
