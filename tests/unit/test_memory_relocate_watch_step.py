from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation.memory_relocate.apply import ApplyOutcome
from automation.memory_relocate.effects_live import RelocationStore
from automation.memory_relocate.model import (
    RelocationRecord,
    RelocationState,
    RelocationStatus,
    record_key,
)
from automation.memory_relocate.store import load_state, save_state
from automation.memory_relocate.watch_step import ResolveEffects, resolve_tick


NOW: Final = datetime(2026, 7, 31, 14, 30, tzinfo=UTC)
NOTE_SHA256: Final = "d" * 64


@dataclass(frozen=True, slots=True)
class FakeEffects:
    """Mutable test fake that records every injected effect call."""

    post_results: list[tuple[str, str] | None] = field(
        default_factory=lambda: [("approval-1", "channel-live")]
    )
    verdict: str = "pending"
    write_results: list[tuple[str, str] | None] = field(
        default_factory=lambda: [("obsidian:note-a", NOTE_SHA256)]
    )
    rag_ready: bool = True
    apply_outcome: ApplyOutcome = field(
        default_factory=lambda: ApplyOutcome(True, None, "/private/backup-a", 42)
    )
    calls: list[tuple[str, str]] = field(default_factory=list)

    def post_approval(self, record: RelocationRecord) -> tuple[str, str] | None:
        self.calls.append(("post", _key(record)))
        return self.post_results.pop(0)

    def probe_reaction(self, record: RelocationRecord) -> str:
        self.calls.append(("probe", _key(record)))
        return self.verdict

    def write_obsidian(self, record: RelocationRecord) -> tuple[str, str] | None:
        self.calls.append(("write", _key(record)))
        return self.write_results.pop(0)

    def verify_rag(self, record: RelocationRecord) -> bool:
        self.calls.append(("verify", _key(record)))
        return self.rag_ready

    def apply_delete(self, record: RelocationRecord) -> ApplyOutcome:
        self.calls.append(("apply", _key(record)))
        return self.apply_outcome

    def resolve_effects(self) -> ResolveEffects:
        return ResolveEffects(
            post_approval=self.post_approval,
            probe_reaction=self.probe_reaction,
            write_obsidian=self.write_obsidian,
            verify_rag=self.verify_rag,
            apply_delete=self.apply_delete,
            now=NOW,
        )


def _record(
    digest_char: str = "a",
    *,
    status: RelocationStatus = "proposed",
    message_id: str | None = None,
) -> RelocationRecord:
    return RelocationRecord(
        version=1,
        source_kind="memory",
        entry_sha256=digest_char * 64,
        note_relpath=f"000_PARA/Resource/note-{digest_char}.md",
        note_plan_sha256="b" * 64,
        reclaimable_chars=321,
        action_hash=f"sha256:{'c' * 64}",
        status=status,
        kind="obsidian-write",
        surface="owner-dm",
        channel_id="owner-dm-1",
        policy_version=1,
        message_id=message_id,
        created_at="2026-07-31T14:00:00+00:00",
        approved_at=None,
        written_at=None,
        reconciled_at=None,
        remote_ref=None,
        note_content_sha256=None,
        rag_source_key=f"obsidian:note-{digest_char}",
        rag_fingerprint=None,
        backup_path=None,
        last_block_reason=None,
    )


def _posted_record(digest_char: str = "a") -> RelocationRecord:
    return replace(_record(digest_char), status="posted", message_id="approval-original")


def _key(record: RelocationRecord) -> str:
    return record_key(record.source_kind, record.entry_sha256)


def _state(*records: RelocationRecord) -> RelocationState:
    return RelocationState(version=1, relocations={_key(record): record for record in records})


def _only(state: RelocationState) -> RelocationRecord:
    return next(iter(state.relocations.values()))


def test_resolve_tick_when_all_gates_pass_advances_full_happy_path_across_ticks() -> None:
    # Given: every injected effect succeeds for one proposed relocation.
    record = _record()
    key = _key(record)
    fake = FakeEffects(verdict="approved")

    # When: four successive cron ticks cross one state boundary each.
    posted = resolve_tick(_state(record), effects=fake.resolve_effects())
    approved = resolve_tick(posted.state, effects=fake.resolve_effects())
    written = resolve_tick(approved.state, effects=fake.resolve_effects())
    reconciled = resolve_tick(written.state, effects=fake.resolve_effects())

    # Then: approval, write, RAG proof, and five-gate delete occur in order.
    assert posted.posted == (key,)
    assert _only(posted.state).message_id == "approval-1"
    assert _only(approved.state).approved_at == NOW.isoformat()
    assert written.written == (key,)
    assert _only(written.state).written_at == NOW.isoformat()
    assert reconciled.reconciled == (key,)
    assert _only(reconciled.state).status == "reconciled"
    assert _only(reconciled.state).backup_path == "/private/backup-a"
    assert fake.calls == [
        ("post", key),
        ("probe", key),
        ("write", key),
        ("verify", key),
        ("apply", key),
    ]


def test_resolve_tick_when_owner_cancels_abandons_record() -> None:
    # Given: cha cancelled a posted, bound approval.
    record = _posted_record()
    fake = FakeEffects(verdict="cancelled")

    # When: the reaction-only tick resolves the verdict.
    result = resolve_tick(_state(record), effects=fake.resolve_effects())

    # Then: the record becomes terminal without any destructive effect.
    assert _only(result.state).status == "abandoned"
    assert result.abandoned == (_key(record),)
    assert fake.calls == [("probe", _key(record))]


def test_resolve_tick_when_approval_message_is_missing_reproposes_and_clears_binding() -> None:
    # Given: the bound Discord message vanished.
    record = _posted_record()
    fake = FakeEffects(verdict="missing")

    # When: the tick probes the missing message.
    result = resolve_tick(_state(record), effects=fake.resolve_effects())

    # Then: it safely re-proposes instead of deleting anything.
    assert _only(result.state).status == "proposed"
    assert _only(result.state).message_id is None
    assert result.reconciled == ()
    assert fake.calls == [("probe", _key(record))]


def test_resolve_tick_when_post_returns_none_leaves_proposed_for_retry() -> None:
    # Given: posting cannot produce a durable message id this tick.
    record = _record()
    fake = FakeEffects(post_results=[None])

    # When: the proposed record is processed.
    result = resolve_tick(_state(record), effects=fake.resolve_effects())

    # Then: no binding or status is invented.
    assert _only(result.state) == record
    assert result.posted == ()


def test_resolve_tick_when_write_retries_only_advances_after_receipt() -> None:
    # Given: the first idempotent Obsidian write has no verified receipt.
    record = _record(status="approved", message_id="approval-1")
    fake = FakeEffects(write_results=[None, ("obsidian:note-a", NOTE_SHA256)])

    # When: two ticks retry the same approved record.
    first = resolve_tick(_state(record), effects=fake.resolve_effects())
    second = resolve_tick(first.state, effects=fake.resolve_effects())

    # Then: only the verified receipt crosses the written boundary.
    assert _only(first.state).status == "approved"
    assert _only(second.state).status == "written"
    assert _only(second.state).note_content_sha256 == NOTE_SHA256
    assert second.written == (_key(record),)
    assert fake.calls == [("write", _key(record)), ("write", _key(record))]


def test_resolve_tick_when_rag_is_not_ready_leaves_written_without_delete() -> None:
    # Given: Obsidian is written but the ten-minute RAG ingest has not completed.
    record = _record(status="written", message_id="approval-1")
    fake = FakeEffects(rag_ready=False)

    # When: the tick checks RAG proof.
    result = resolve_tick(_state(record), effects=fake.resolve_effects())

    # Then: native deletion is not called and the record remains retryable.
    assert _only(result.state) == record
    assert result.reconciled == ()
    assert fake.calls == [("verify", _key(record))]


def test_resolve_tick_when_delete_gate_blocks_keeps_written_reason_and_native_state() -> None:
    # Given: RAG is ready but singular native-target proof blocks the five-gate delete.
    record = _record(status="written", message_id="approval-1")
    fake = FakeEffects(
        apply_outcome=ApplyOutcome(False, "entry_ambiguous", None, 0),
    )

    # When: the destructive seam returns its fail-closed outcome.
    result = resolve_tick(_state(record), effects=fake.resolve_effects())

    # Then: no reconciliation is claimed and the block remains visible for retry.
    assert _only(result.state).status == "written"
    assert _only(result.state).last_block_reason == "entry_ambiguous"
    assert result.reconciled == ()
    assert fake.calls == [("verify", _key(record)), ("apply", _key(record))]


def test_resolve_tick_when_three_are_proposed_posts_only_first_sorted_key() -> None:
    # Given: three proposals arrive in reverse key order.
    records = (_record("c"), _record("b"), _record("a"))
    fake = FakeEffects(
        post_results=[("approval-a", "channel-a"), ("approval-b", "channel-b")],
    )

    # When: the default one-post flood cap processes the state.
    result = resolve_tick(_state(*records), effects=fake.resolve_effects())

    # Then: deterministic key order selects only a and leaves b/c proposed.
    first_key = _key(records[2])
    assert result.posted == (first_key,)
    assert fake.calls == [("post", first_key)]
    assert result.state.relocations[first_key].status == "posted"
    assert result.state.relocations[_key(records[1])].status == "proposed"
    assert result.state.relocations[_key(records[0])].status == "proposed"


def test_resolve_tick_when_record_is_already_posted_never_reposts_or_overwrites_message_id() -> None:
    # Given: a posted record already has its immutable Discord binding.
    record = _posted_record()
    fake = FakeEffects(
        post_results=[("approval-replacement", "channel-live")],
        verdict="pending",
    )

    # When: another tick sees no terminal owner reaction.
    result = resolve_tick(_state(record), effects=fake.resolve_effects())

    # Then: only reaction probing occurs and the original binding survives.
    assert _only(result.state).message_id == "approval-original"
    assert result.posted == ()
    assert fake.calls == [("probe", _key(record))]


def test_resolve_tick_when_posting_persists_the_channel_the_approval_was_posted_to() -> None:
    # Given: autonomous discovery proposed the record before any approval surface existed.
    record = replace(_record(), channel_id="")
    fake = FakeEffects(post_results=[("approval-1", "channel-live")])

    # When: the tick posts the approval and learns the surface it actually landed on.
    result = resolve_tick(_state(record), effects=fake.resolve_effects())

    # Then: the whole binding is on the record — the message AND the channel it was posted to.
    posted_record = _only(result.state)
    assert posted_record.status == "posted"
    assert posted_record.message_id == "approval-1"
    assert posted_record.channel_id == "channel-live"


def test_resolve_tick_when_post_returns_an_empty_channel_leaves_proposed() -> None:
    # Given: the posting seam cannot name the surface it posted to.
    record = _record()
    fake = FakeEffects(post_results=[("approval-1", "")])

    # When: the proposed record is processed.
    result = resolve_tick(_state(record), effects=fake.resolve_effects())

    # Then: half a binding is never written — an empty channel reads MISSING on the next tick
    # and discards the owner's ✅.
    assert _only(result.state).status == "proposed"
    assert _only(result.state).message_id is None
    assert result.posted == ()


def test_tick_state_save_does_not_clobber_the_stores_channel_binding(tmp_path: Path) -> None:
    # Given: a persisted proposal with no surface yet, and the production posting seam, which
    # binds message AND channel through the store while the tick still holds its pre-post snapshot.
    path = tmp_path / "relocations.json"
    record = replace(_record(), channel_id="")
    save_state(path, _state(record))
    store = RelocationStore(path)

    def post(pending: RelocationRecord) -> tuple[str, str]:
        store.set_message_id(pending, "msg-1", "chan-9")
        return ("msg-1", "chan-9")

    effects = replace(FakeEffects().resolve_effects(), post_approval=post)

    # When: the cron's exact sequence runs — tick, then persist the tick's own snapshot.
    result = resolve_tick(load_state(path), effects=effects)
    save_state(path, result.state)

    # Then: the saved snapshot still carries the surface the approval was posted to.
    stored = _only(load_state(path))
    assert stored.message_id == "msg-1"
    assert stored.channel_id == "chan-9"
    assert stored.status == "posted"
