from __future__ import annotations

from dataclasses import replace
from urllib.error import HTTPError

import pytest

from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalRequest,
    ApprovalSurfaceError,
    PostedApproval,
    Probe,
)
from automation.memory_relocate.approval_gate import (
    APPROVE_EMOJI,
    CANCEL_EMOJI,
    RelocateApprovalGate,
)
from automation.memory_relocate.model import RelocationRecord, record_key
from automation.memory_relocate.render import render_relocation_approval


def _record(*, message_id: str | None = None) -> RelocationRecord:
    return RelocationRecord(
        version=1,
        source_kind="memory",
        entry_sha256="a" * 64,
        note_relpath="Areas/research-memory.md",
        note_plan_sha256="b" * 64,
        reclaimable_chars=321,
        action_hash=f"sha256:{'c' * 64}",
        status="proposed",
        kind="memory_relocation",
        surface="owner_dm",
        channel_id="123456789",
        policy_version=6,
        message_id=message_id,
        created_at="2026-07-31T10:00:00Z",
        approved_at=None,
        written_at=None,
        reconciled_at=None,
        remote_ref=None,
        note_content_sha256=None,
        rag_source_key=None,
        rag_fingerprint=None,
        backup_path=None,
        last_block_reason=None,
    )


class FakeStore:
    """Mutable in-memory fake that enforces message-id bind and CAS rules."""

    def __init__(self, records: tuple[RelocationRecord, ...]) -> None:
        self.records: list[RelocationRecord] = list(records)
        self.set_calls: list[tuple[RelocationRecord, str]] = []
        self.clear_calls: list[tuple[str, str, str]] = []

    def pending(self) -> tuple[RelocationRecord, ...]:
        return tuple(self.records)

    def set_message_id(
        self, record: RelocationRecord, message_id: str, channel_id: str
    ) -> None:
        key = record_key(record.source_kind, record.entry_sha256)
        for index, current in enumerate(self.records):
            if record_key(current.source_kind, current.entry_sha256) != key:
                continue
            if current.message_id is not None:
                raise RuntimeError("message id is already bound")
            self.records[index] = replace(
                current, message_id=message_id, channel_id=channel_id
            )
            self.set_calls.append((record, message_id))
            return
        raise RuntimeError("record is absent")

    def clear_message_id(self, key: str, action_hash: str, message_id: str) -> None:
        self.clear_calls.append((key, action_hash, message_id))
        for index, current in enumerate(self.records):
            current_key = record_key(current.source_kind, current.entry_sha256)
            if (current_key, current.action_hash, current.message_id) == (
                key,
                action_hash,
                message_id,
            ):
                self.records[index] = replace(current, message_id=None)
                return


class FakeTransport:
    """Mutable Discord fake; no method crosses the host boundary."""

    owner_id: str = "owner-1"

    def __init__(self) -> None:
        self.messages: dict[tuple[str, str], str] = {}
        self.reactions: dict[
            tuple[str, str, str], tuple[tuple[str, bool], ...]
        ] = {}
        self.posts: list[tuple[str, str]] = []
        self.added: list[tuple[str, str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self.message_error: OSError | None = None

    def post_message(self, channel_id: str, content: str) -> str:
        message_id = "posted-1"
        self.posts.append((channel_id, content))
        self.messages[(channel_id, message_id)] = content
        return message_id

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.added.append((channel_id, message_id, emoji))

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        if self.message_error is not None:
            raise self.message_error
        return self.messages.get((channel_id, message_id))

    def get_reaction_users(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> tuple[tuple[str, bool], ...]:
        return self.reactions.get((channel_id, message_id, emoji), ())

    def delete_message(self, channel_id: str, message_id: str) -> None:
        self.deleted.append((channel_id, message_id))


def _gate(
    record: RelocationRecord | None = None,
) -> tuple[RelocateApprovalGate, FakeStore, FakeTransport]:
    selected = _record(message_id="message-1") if record is None else record
    store = FakeStore((selected,))
    transport = FakeTransport()
    return RelocateApprovalGate(selected, "원본 메모리 항목", store, transport), store, transport


def _request(record: RelocationRecord) -> ApprovalRequest:
    assert record.message_id is not None
    return ApprovalRequest(
        key=record_key(record.source_kind, record.entry_sha256),
        action_hash=record.action_hash,
        message_id=record.message_id,
        channel_id=record.channel_id,
        created_at=record.created_at,
    )


def test_post_when_intent_is_new_renders_posts_and_adds_both_reactions() -> None:
    # Given: an unbound relocation and a host-only Discord fake.
    record = _record()
    gate, _, transport = _gate(record)
    intent = ApprovalIntent(
        record_key(record.source_kind, record.entry_sha256),
        record.action_hash,
        record.channel_id,
    )

    # When: the lifecycle asks the adapter to post.
    posted = gate.post(intent)

    # Then: the frozen raw-entry rendering is posted with both owner decisions.
    expected = render_relocation_approval(record, entry_text="원본 메모리 항목")
    assert posted == PostedApproval("posted-1", record.channel_id)
    assert transport.posts == [(record.channel_id, expected)]
    assert record.action_hash in expected
    assert "원본 메모리 항목" in expected
    assert transport.added == [
        (record.channel_id, "posted-1", APPROVE_EMOJI),
        (record.channel_id, "posted-1", CANCEL_EMOJI),
    ]


@pytest.mark.parametrize(
    ("approved", "cancelled", "expected"),
    [
        ((("owner-1", False),), (), Probe.APPROVED),
        ((), (("owner-1", False),), Probe.CANCELLED),
        ((("owner-1", False),), (("owner-1", False),), Probe.CANCELLED),
        ((), (), Probe.BOUND_PENDING),
        ((("owner-1", True), ("other", False)), (), Probe.BOUND_PENDING),
    ],
)
def test_probe_when_reactions_vary_accepts_only_owner_with_cancel_precedence(
    approved: tuple[tuple[str, bool], ...],
    cancelled: tuple[tuple[str, bool], ...],
    expected: Probe,
) -> None:
    # Given: a correctly bound approval message with selected reaction users.
    record = _record(message_id="message-1")
    gate, _, transport = _gate(record)
    transport.messages[(record.channel_id, "message-1")] = record.action_hash
    transport.reactions[(record.channel_id, "message-1", APPROVE_EMOJI)] = approved
    transport.reactions[(record.channel_id, "message-1", CANCEL_EMOJI)] = cancelled

    # When: the request is probed.
    result = gate.probe(_request(record))

    # Then: only cha's non-bot decision counts and cancellation wins.
    assert result is expected


def test_probe_when_message_is_missing_returns_missing() -> None:
    record = _record(message_id="message-1")
    gate, _, _ = _gate(record)
    assert gate.probe(_request(record)) is Probe.MISSING


def test_probe_when_content_does_not_bind_action_returns_mismatch() -> None:
    record = _record(message_id="message-1")
    gate, _, transport = _gate(record)
    transport.messages[(record.channel_id, "message-1")] = "different content"
    assert gate.probe(_request(record)) is Probe.BINDING_MISMATCH


def test_probe_when_transport_fails_raises_surface_error() -> None:
    record = _record(message_id="message-1")
    gate, _, transport = _gate(record)
    transport.message_error = OSError("Discord unavailable")
    with pytest.raises(ApprovalSurfaceError):
        _ = gate.probe(_request(record))


def test_commit_when_record_becomes_bound_never_overwrites_message_id() -> None:
    # Given: an unbound relocation record.
    record = _record()
    gate, store, _ = _gate(record)
    intent = ApprovalIntent("memory:key", record.action_hash, record.channel_id)

    # When: commit is attempted twice with different Discord message ids.
    gate.commit(intent, PostedApproval("first", record.channel_id), record.created_at)
    with pytest.raises(RuntimeError):
        gate.commit(intent, PostedApproval("second", record.channel_id), record.created_at)

    # Then: the first binding is preserved and was the only successful write.
    assert store.records[0].message_id == "first"
    assert store.set_calls == [(record, "first")]


def test_outstanding_and_drop_use_record_binding_and_compare_and_swap() -> None:
    # Given: one bound pending relocation.
    record = _record(message_id="message-1")
    gate, store, _ = _gate(record)

    # When: it is projected and then dropped by the shared lifecycle.
    requests = gate.outstanding(record_key(record.source_kind, record.entry_sha256))
    gate.drop(requests[0])

    # Then: every persisted binding field feeds the request and CAS unbind.
    assert requests == (_request(record),)
    assert store.clear_calls == [
        (requests[0].key, record.action_hash, "message-1")
    ]
    assert store.records[0].message_id is None


class _RecordingJournal:
    def __init__(self, calls: list[tuple[str, ...]]) -> None:
        self.calls = calls

    def enrich(
        self,
        key: str,
        action_hash: str,
        message_id: str,
        channel_id: str,
    ) -> None:
        self.calls.append(("enrich", key, action_hash, message_id, channel_id))

    def clear(self, key: str) -> None:
        self.calls.append(("clear", key))


class _OrderedTransport(FakeTransport):
    def __init__(self, calls: list[tuple[str, ...]]) -> None:
        super().__init__()
        self.calls = calls

    def post_message(self, channel_id: str, content: str) -> str:
        self.calls.append(("post", channel_id))
        return super().post_message(channel_id, content)

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.calls.append(("reaction", channel_id, message_id, emoji))
        super().add_reaction(channel_id, message_id, emoji)


def test_post_when_journal_is_injected_enriches_receipt_before_reactions() -> None:
    # Given: the memory adapter has a reserved journal and an unbound relocation.
    record = _record()
    store = FakeStore((record,))
    calls: list[tuple[str, ...]] = []
    transport = _OrderedTransport(calls)
    journal = _RecordingJournal(calls)
    gate = RelocateApprovalGate(record, "원본 메모리 항목", store, transport, journal)
    intent = ApprovalIntent(
        record_key(record.source_kind, record.entry_sha256),
        record.action_hash,
        record.channel_id,
    )

    # When: Discord accepts the POST.
    posted = gate.post(intent)

    # Then: the receipt reaches durable enrichment before either reaction is attempted.
    assert posted == PostedApproval("posted-1", record.channel_id)
    assert calls[0] == ("post", record.channel_id)
    assert calls[1] == (
        "enrich",
        intent.key,
        intent.action_hash,
        "posted-1",
        record.channel_id,
    )
    names = [call[0] for call in calls]
    assert names.index("enrich") < names.index("reaction")


class _OrderedStore(FakeStore):
    def __init__(
        self,
        records: tuple[RelocationRecord, ...],
        calls: list[tuple[str, ...]],
    ) -> None:
        super().__init__(records)
        self.calls = calls

    def set_message_id(
        self,
        record: RelocationRecord,
        message_id: str,
        channel_id: str,
    ) -> None:
        super().set_message_id(record, message_id, channel_id)
        self.calls.append(("commit", message_id, channel_id))


class _ReactionFailingTransport(_OrderedTransport):
    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.calls.append(("reaction", channel_id, message_id, emoji))
        raise OSError("reaction unavailable")


def test_post_when_reactions_fail_commits_and_clears_before_best_effort_attach() -> None:
    # Given: Discord accepts the message but rejects both reaction PUTs.
    record = _record()
    calls: list[tuple[str, ...]] = []
    store = _OrderedStore((record,), calls)
    journal = _RecordingJournal(calls)
    transport = _ReactionFailingTransport(calls)
    gate = RelocateApprovalGate(record, "원본 메모리 항목", store, transport, journal)
    intent = ApprovalIntent(
        record_key(record.source_kind, record.entry_sha256),
        record.action_hash,
        record.channel_id,
    )

    # When: the adapter performs its atomic posting sequence.
    posted = gate.post(intent)

    # Then: reaction failure is not POST failure and the binding precedes every reaction.
    assert posted == PostedApproval("posted-1", record.channel_id)
    assert [call[0] for call in calls] == [
        "post",
        "enrich",
        "commit",
        "clear",
        "reaction",
        "reaction",
    ]
    assert store.records[0].message_id == "posted-1"
    assert store.records[0].channel_id == record.channel_id


def test_probe_when_binding_is_live_idempotently_repairs_bot_reactions() -> None:
    # Given: a correctly bound message lacks the bot's two affordance reactions.
    record = _record(message_id="message-1")
    gate, _, transport = _gate(record)
    transport.messages[(record.channel_id, "message-1")] = record.action_hash

    # When: the next tick probes the owner decision.
    verdict = gate.probe(_request(record))

    # Then: idempotent PUTs repair both affordances before returning pending.
    assert verdict is Probe.BOUND_PENDING
    assert transport.added == [
        (record.channel_id, "message-1", APPROVE_EMOJI),
        (record.channel_id, "message-1", CANCEL_EMOJI),
    ]


class _HttpMissingTransport(FakeTransport):
    def get_message(self, channel_id: str, message_id: str) -> str | None:
        del channel_id, message_id
        raise HTTPError("https://discord.invalid/message", 404, "Unknown Message", {}, None)


def test_probe_when_discord_returns_exact_404_remains_missing_fail_closed() -> None:
    # Given: Discord reports the bound message as exactly missing.
    record = _record(message_id="message-1")
    store = FakeStore((record,))
    transport = _HttpMissingTransport()
    gate = RelocateApprovalGate(record, "원본 메모리 항목", store, transport)

    # When: the adapter probes the exact stored binding.
    verdict = gate.probe(_request(record))

    # Then: the existing fail-closed MISSING transition is preserved.
    assert verdict is Probe.MISSING
    assert transport.added == []
