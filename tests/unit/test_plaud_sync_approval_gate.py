from __future__ import annotations

from dataclasses import replace
from email.message import Message
from urllib.error import HTTPError

import pytest

from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalRequest,
    ApprovalSurfaceError,
    Probe,
)
from automation.plaud_sync.approval_gate import (
    APPROVE_EMOJI,
    CANCEL_EMOJI,
    PlaudApprovalGate,
)
from automation.plaud_sync.model import PlaudSyncRecord
from automation.plaud_sync.render import render_plaud_approval

_OWNER = "owner-1"


_BASE = PlaudSyncRecord(
    version=1,
    recording_id="rec-001",
    recorded_at="2026-09-01T08:00:00Z",
    note_relpath="000_PARA/Area/Lifelog/2026/2026-09-01-standup--abcdef123456.md",
    note_title="standup (2026-09-01)",
    body_sha256="a" * 64,
    action_hash=f"sha256:{'b' * 64}",
    status="posted",
    kind="obsidian-write",
    surface="agent-chat-thread",
    channel_id="chan-1",
    policy_version=8,
    message_id="msg-1",
    created_at="2026-09-01T09:00:00Z",
    approved_at=None,
    written_at=None,
    remote_ref=None,
    note_content_sha256=None,
    last_block_reason=None,
)


def _record(**overrides: object) -> PlaudSyncRecord:
    return replace(_BASE, **overrides)


def _request(record: PlaudSyncRecord) -> ApprovalRequest:
    assert record.message_id is not None
    return ApprovalRequest(
        key=record.recording_id,
        action_hash=record.action_hash,
        message_id=record.message_id,
        channel_id=record.channel_id,
        created_at=record.created_at,
    )


class FakeStore:
    def __init__(self, records: tuple[PlaudSyncRecord, ...]) -> None:
        self.records = list(records)
        self.set_calls: list[tuple[str, str, str]] = []
        self.clear_calls: list[tuple[str, str, str]] = []

    def pending(self) -> tuple[PlaudSyncRecord, ...]:
        return tuple(self.records)

    def set_message_id(
        self, record: PlaudSyncRecord, message_id: str, channel_id: str
    ) -> None:
        self.set_calls.append((record.recording_id, message_id, channel_id))

    def clear_message_id(self, key: str, action_hash: str, message_id: str) -> None:
        self.clear_calls.append((key, action_hash, message_id))


class FakeTransport:
    owner_id = _OWNER

    def __init__(
        self,
        *,
        content: str | None = None,
        approvers: tuple[tuple[str, bool], ...] = (),
        cancellers: tuple[tuple[str, bool], ...] = (),
    ) -> None:
        self.content = content
        self.approvers = approvers
        self.cancellers = cancellers
        self.posted: list[tuple[str, str]] = []
        self.reactions: list[tuple[str, str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    def post_message(self, channel_id: str, content: str) -> str:
        self.posted.append((channel_id, content))
        return "msg-new"

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.reactions.append((channel_id, message_id, emoji))

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        if isinstance(self.content, Exception):
            raise self.content
        return self.content

    def get_reaction_users(
        self, channel_id: str, message_id: str, emoji: str
    ) -> tuple[tuple[str, bool], ...]:
        if emoji == APPROVE_EMOJI:
            return self.approvers
        return self.cancellers

    def delete_message(self, channel_id: str, message_id: str) -> None:
        self.deleted.append((channel_id, message_id))


class FakeJournal:
    def __init__(self) -> None:
        self.enriched: list[tuple[str, str, str, str]] = []
        self.cleared: list[str] = []

    def enrich(self, key: str, action_hash: str, message_id: str, channel_id: str) -> None:
        self.enriched.append((key, action_hash, message_id, channel_id))

    def clear(self, key: str) -> None:
        self.cleared.append(key)


def _gate(
    record: PlaudSyncRecord,
    transport: FakeTransport,
    store: FakeStore | None = None,
    journal: FakeJournal | None = None,
) -> PlaudApprovalGate:
    return PlaudApprovalGate(
        record=record,
        store=store or FakeStore((record,)),
        transport=transport,
        journal=journal,
    )


def _bound_content(record: PlaudSyncRecord) -> str:
    return render_plaud_approval(record)


def test_probe_owner_approval_is_approved() -> None:
    record = _record()
    transport = FakeTransport(content=_bound_content(record), approvers=((_OWNER, False),))
    assert _gate(record, transport).probe(_request(record)) is Probe.APPROVED


def test_probe_cancel_wins_over_approval() -> None:
    record = _record()
    transport = FakeTransport(
        content=_bound_content(record),
        approvers=((_OWNER, False),),
        cancellers=((_OWNER, False),),
    )
    assert _gate(record, transport).probe(_request(record)) is Probe.CANCELLED


def test_probe_ignores_bots_and_strangers() -> None:
    record = _record()
    transport = FakeTransport(
        content=_bound_content(record),
        approvers=(("bot-1", True), ("someone-else", False)),
    )
    assert _gate(record, transport).probe(_request(record)) is Probe.BOUND_PENDING


def test_probe_missing_message_is_missing() -> None:
    record = _record()
    transport = FakeTransport(content=None)
    assert _gate(record, transport).probe(_request(record)) is Probe.MISSING


def test_probe_foreign_content_is_binding_mismatch() -> None:
    record = _record()
    transport = FakeTransport(content="totally unrelated message")
    assert _gate(record, transport).probe(_request(record)) is Probe.BINDING_MISMATCH


def test_probe_transport_failure_is_surface_error() -> None:
    record = _record()
    transport = FakeTransport(content=_bound_content(record))

    def _boom(channel_id: str, message_id: str, emoji: str) -> None:
        raise OSError("discord unreachable")

    transport.add_reaction = _boom  # type: ignore[method-assign]
    with pytest.raises(ApprovalSurfaceError):
        _ = _gate(record, transport).probe(_request(record))


def test_post_renders_card_binds_store_and_preattaches_reactions() -> None:
    record = _record(message_id=None, channel_id="")
    transport = FakeTransport()
    store = FakeStore((record,))
    journal = FakeJournal()
    gate = _gate(record, transport, store=store, journal=journal)
    intent = ApprovalIntent(
        key=record.recording_id, action_hash=record.action_hash, channel_id="chan-9"
    )
    posted = gate.post(intent)
    assert (posted.message_id, posted.channel_id) == ("msg-new", "chan-9")
    assert transport.posted == [("chan-9", render_plaud_approval(record))]
    assert ("rec-001", "msg-new", "chan-9") in store.set_calls
    assert journal.enriched == [(record.recording_id, record.action_hash, "msg-new", "chan-9")]
    assert journal.cleared == [record.recording_id]
    emojis = {reaction[2] for reaction in transport.reactions}
    assert emojis == {APPROVE_EMOJI, CANCEL_EMOJI}


def test_outstanding_lists_only_bound_records_for_key() -> None:
    bound = _record()
    unbound = _record(recording_id="rec-002", message_id=None)
    gate = _gate(bound, FakeTransport(), store=FakeStore((bound, unbound)))
    requests = gate.outstanding("rec-001")
    assert [request.message_id for request in requests] == ["msg-1"]
    assert gate.outstanding("rec-002") == ()


def test_delete_tolerates_already_gone_message() -> None:
    record = _record()
    transport = FakeTransport()

    def _gone(channel_id: str, message_id: str) -> None:
        raise HTTPError("https://discord.com", 404, "gone", Message(), None)

    transport.delete_message = _gone  # type: ignore[method-assign]
    _gate(record, transport).delete(_request(record))


def test_drop_requests_compare_and_swap_unbind() -> None:
    record = _record()
    store = FakeStore((record,))
    _gate(record, FakeTransport(), store=store).drop(_request(record))
    assert store.clear_calls == [("rec-001", record.action_hash, "msg-1")]


def test_post_quotes_the_preview_handed_to_the_gate() -> None:
    record = _record(message_id=None, channel_id="")
    transport = FakeTransport()
    gate = PlaudApprovalGate(
        record=record,
        store=FakeStore((record,)),
        transport=transport,
        journal=FakeJournal(),
        preview="- 첫째 줄\n- 둘째 줄",
    )
    intent = ApprovalIntent(
        key=record.recording_id, action_hash=record.action_hash, channel_id="chan-9"
    )
    _ = gate.post(intent)
    assert transport.posted == [
        ("chan-9", render_plaud_approval(record, preview="- 첫째 줄\n- 둘째 줄"))
    ]
    assert "> - 첫째 줄\n> - 둘째 줄" in transport.posted[0][1]
