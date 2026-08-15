"""One live repair approval request per ticket — supersede, re-post, defer, refuse."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from automation.interop.approval_lease import FileKeyLease
from automation.repair.repair_ops_approval_gate import lease_root, repair_approval_key
from automation.repair.repair_ops_pending import (
    APPROVE_EMOJI,
    PendingRepairApproval,
    PendingRepairApprovalStore,
    PostingOwnerApproval,
)

OWNER_ID = "280680578314010625"
NOW = datetime(2026, 7, 26, 9, 0, tzinfo=UTC)
TICKET = "t-repair-1"

# The approval is bound to the patch BYTES, so these must be real files. The
# "other" body keeps the same file name on purpose: a same-name content swap is
# exactly the case the binding exists to catch.
PATCH_BODY = (
    "diff --git a/automation/mod.py b/automation/mod.py\n"
    "--- a/automation/mod.py\n"
    "+++ b/automation/mod.py\n"
    "@@ -1,2 +1,2 @@\n"
    " context\n"
    "-old\n"
    "+new\n"
)
OTHER_BODY = PATCH_BODY.replace("+new", "+something else entirely")


def _patch(tmp_path: Path, body: str = PATCH_BODY) -> Path:
    target = tmp_path / "plans" / TICKET / "patch.diff"
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = target.write_text(body, encoding="utf-8")
    return target


@dataclass
class FakeApprovalSurface:
    """Offline stand-in for the full repair #approvals surface (post + poll + delete)."""

    calls: list[str] = field(default_factory=list)
    messages: dict[str, str] = field(default_factory=dict)
    reactions: dict[tuple[str, str], tuple[tuple[str, bool], ...]] = field(default_factory=dict)
    posts: list[str] = field(default_factory=list)
    added: list[tuple[str, str]] = field(default_factory=list)

    def post_approval(self, content: str) -> str:
        message_id = f"msg-{len(self.posts) + 1}"
        self.posts.append(content)
        self.messages[message_id] = content
        self.calls.append(f"post:{message_id}")
        return message_id

    def add_reaction(self, message_id: str, emoji: str) -> None:
        self.added.append((message_id, emoji))

    def content(self, message_id: str) -> str:
        return self.messages.get(message_id, "")

    def reaction_users(self, message_id: str, emoji: str) -> tuple[tuple[str, bool], ...]:
        return self.reactions.get((message_id, emoji), ())

    def delete_message(self, message_id: str) -> None:
        self.calls.append(f"delete:{message_id}")
        _ = self.messages.pop(message_id, None)


@dataclass
class RecordingStore:
    """Delegating pending store that timestamps its own mutations in a shared call log."""

    inner: PendingRepairApprovalStore
    calls: list[str]

    @property
    def root(self) -> Path:
        return self.inner.root

    def get(self, ticket_id: str) -> PendingRepairApproval | None:
        return self.inner.get(ticket_id)

    def all_strict(self) -> tuple[PendingRepairApproval, ...]:
        return self.inner.all_strict()

    def save(self, pending: PendingRepairApproval) -> None:
        self.calls.append(f"save:{pending.message_id}")
        self.inner.save(pending)

    def drop(self, pending: PendingRepairApproval) -> None:
        self.calls.append(f"drop:{pending.message_id}")
        self.inner.drop(pending)


def _nonces() -> list[int]:
    return [0]


def _posting(root: Path, surface: FakeApprovalSurface, calls: list[str]) -> PostingOwnerApproval:
    counter = _nonces()

    def nonce() -> str:
        counter[0] += 1
        return f"{counter[0]:032x}"

    store = RecordingStore(PendingRepairApprovalStore(root / "pending"), calls)
    return PostingOwnerApproval(OWNER_ID, store, surface, now=lambda: NOW, nonce=nonce)


def _live(root: Path) -> tuple[Path, ...]:
    return tuple(sorted((root / "pending").glob("*.json")))


def test_same_ticket_and_hash_when_requested_again_then_posts_nothing_and_keeps_the_record(
    tmp_path: Path,
) -> None:
    # Given: one live request already posted for this exact ticket and patch
    calls: list[str] = []
    surface = FakeApprovalSurface()
    approval = _posting(tmp_path, surface, calls)
    assert approval.permits(TICKET, _patch(tmp_path)) is False
    before = approval.store.get(TICKET)

    # When: the same ticket reaches the approval boundary with unchanged content
    permitted = approval.permits(TICKET, _patch(tmp_path))

    # Then: the legacy refusal holds — no post, no delete, byte-identical record
    assert permitted is False
    assert surface.posts == [surface.posts[0]]
    assert [call for call in surface.calls if call.startswith("delete")] == []
    assert approval.store.get(TICKET) == before
    assert len(_live(tmp_path)) == 1


def test_ticket_content_changed_when_requested_then_deletes_before_dropping_and_reposts_once(
    tmp_path: Path,
) -> None:
    # Given: a live request bound to the previous patch of the same ticket
    calls: list[str] = []
    surface = FakeApprovalSurface(calls=calls)
    approval = _posting(tmp_path, surface, calls)
    assert approval.permits(TICKET, _patch(tmp_path)) is False
    first = approval.store.get(TICKET)
    assert first is not None

    # When: the ticket's patch changes while that request is still outstanding
    permitted = approval.permits(TICKET, _patch(tmp_path, OTHER_BODY))

    # Then: the stale message dies BEFORE its record, and exactly one new request lives
    assert permitted is False
    assert calls.index("delete:msg-1") < calls.index("drop:msg-1") < calls.index("post:msg-2")
    assert len(surface.posts) == 2
    assert len(_live(tmp_path)) == 1
    record = approval.store.get(TICKET)
    assert record is not None
    assert record.message_id == "msg-2"
    assert record.patch_name == "patch.diff"
    assert record.action_hash != first.action_hash


def test_owner_already_approved_when_content_changes_then_defers_without_touching_the_request(
    tmp_path: Path,
) -> None:
    # Given: cha already reacted ✅ on the live request for this ticket
    calls: list[str] = []
    surface = FakeApprovalSurface(calls=calls)
    approval = _posting(tmp_path, surface, calls)
    assert approval.permits(TICKET, _patch(tmp_path)) is False
    surface.reactions[("msg-1", APPROVE_EMOJI)] = ((OWNER_ID, False),)
    before = approval.store.get(TICKET)

    # When: a changed patch tries to supersede the already-decided request
    permitted = approval.permits(TICKET, _patch(tmp_path, OTHER_BODY))

    # Then: the owner's decision is preserved for the watcher — nothing is destroyed
    assert permitted is False
    assert "delete:msg-1" not in calls
    assert "msg-1" in surface.messages
    assert approval.store.get(TICKET) == before
    assert len(surface.posts) == 1


def test_message_deleted_out_from_under_the_record_when_requested_then_reposts_and_rebinds(
    tmp_path: Path,
) -> None:
    # Given: the approval message vanished from #approvals but its record survives
    calls: list[str] = []
    surface = FakeApprovalSurface(calls=calls)
    approval = _posting(tmp_path, surface, calls)
    assert approval.permits(TICKET, _patch(tmp_path)) is False
    _ = surface.messages.pop("msg-1")

    # When: the same ticket reaches the approval boundary again
    permitted = approval.permits(TICKET, _patch(tmp_path))

    # Then: the stranded record is dropped and one fresh request is bound
    assert permitted is False
    assert calls.index("drop:msg-1") < calls.index("post:msg-2")
    assert len(_live(tmp_path)) == 1
    record = approval.store.get(TICKET)
    assert record is not None
    assert record.message_id == "msg-2"


def test_corrupt_record_when_requested_then_refuses_and_posts_nothing(tmp_path: Path) -> None:
    # Given: a pending record that cannot be decoded
    calls: list[str] = []
    surface = FakeApprovalSurface(calls=calls)
    approval = _posting(tmp_path, surface, calls)
    pending = tmp_path / "pending"
    pending.mkdir(mode=0o700, parents=True, exist_ok=True)
    corrupt = pending / f"{hashlib.sha256(TICKET.encode()).hexdigest()}.json"
    _ = corrupt.write_text("{not json", encoding="utf-8")

    # When: the ticket reaches the approval boundary
    permitted = approval.permits(TICKET, _patch(tmp_path))

    # Then: an unreadable store is refused, never treated as "nothing outstanding"
    assert permitted is False
    assert surface.posts == []
    assert corrupt.read_text(encoding="utf-8") == "{not json"


def test_watcher_holding_the_shared_lease_when_producer_runs_then_changes_nothing(
    tmp_path: Path,
) -> None:
    # Given: a long-running watcher tick owns this ticket's lease
    calls: list[str] = []
    surface = FakeApprovalSurface(calls=calls)
    approval = _posting(tmp_path, surface, calls)
    lease = FileKeyLease(lease_root(approval.store.root))

    # When: the producer reaches the approval boundary during that tick
    with lease.hold(repair_approval_key(TICKET)) as owned:
        assert owned is True
        permitted = approval.permits(TICKET, _patch(tmp_path))

    # Then: it defers instead of racing — no post and no record
    assert permitted is False
    assert surface.posts == []
    assert _live(tmp_path) == ()
