"""Repair adapter for the shared owner-approval lifecycle.

One live approval request per ticket: content change supersedes (delete BEFORE
drop), a vanished message is re-posted, an already-decided request is deferred to
the watcher, and an unreadable record is refused rather than read as "nothing
outstanding".
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, runtime_checkable
from urllib.error import HTTPError

from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalRecordsError,
    ApprovalRequest,
    ApprovalSurfaceError,
    PostedApproval,
    Probe,
)
from automation.interop.approval_surface import ApprovalBinding
from automation.repair.repair_ops_discord import RepairDiscordError
from automation.repair.repair_ops_pending import (
    APPROVE_EMOJI,
    CANCEL_EMOJI,
    ApprovalRequestTransport,
    PendingRepairApproval,
    PendingRepairApprovalStore,
    approval_request_content,
)
from automation.repair.repair_patch_binding import BINDING_VERSION as CONTENT_BINDING_VERSION
from automation.repair.repair_patch_binding import PatchFileDelta

KEY_PREFIX: Final = "repair:"
_TRANSPORT_ERRORS: Final = (RepairDiscordError, OSError, json.JSONDecodeError, KeyError, TypeError)


def repair_approval_key(ticket_id: str) -> str:
    """Return the logical approval key one repair ticket owns for its whole life."""
    return f"{KEY_PREFIX}{ticket_id}"


def lease_root(pending_root: Path) -> Path:
    """Return the producer/watcher lease dir — a sibling, never inside the record glob."""
    return pending_root.parent / "repair-approval-leases"


def journal_root(pending_root: Path) -> Path:
    """Return the posting-journal dir — a sibling, never inside the record glob."""
    return pending_root.parent / "repair-approval-journal"


def owner_reacted(users: tuple[tuple[str, bool], ...], owner_id: str) -> bool:
    """Accept only cha's own non-bot reaction as an owner decision."""
    return any(user_id == owner_id and not bot for user_id, bot in users)


@runtime_checkable
class ApprovalPollSurface(Protocol):
    """Read-only surface required before any request may be called live or dead."""

    def content(self, message_id: str) -> str: ...

    def reaction_users(self, message_id: str, emoji: str) -> tuple[tuple[str, bool], ...]: ...


@runtime_checkable
class ApprovalDeleteSurface(Protocol):
    """Supersede surface required before a stale request may be destroyed."""

    def delete_message(self, message_id: str) -> None: ...


@runtime_checkable
class PendingBoundTransport(Protocol):
    """Bind a transport to the approval facts persisted on one pending record."""

    def for_pending(self, pending: PendingRepairApproval) -> ApprovalRequestTransport: ...


def probe_pending(
    pending: PendingRepairApproval, owner_id: str, transport: ApprovalRequestTransport
) -> Probe:
    """Classify one stored request against its persisted approval binding."""
    if not isinstance(transport, ApprovalPollSurface):
        raise ApprovalSurfaceError("repair approval transport cannot read the bound request")
    try:
        content = transport.content(pending.message_id)
    except HTTPError as error:
        if error.code == 404:
            return Probe.MISSING
        raise ApprovalSurfaceError(str(error)) from error
    except _TRANSPORT_ERRORS as error:
        raise ApprovalSurfaceError(str(error)) from error
    if not content:
        return Probe.MISSING
    if content != approval_request_content(pending):
        return Probe.BINDING_MISMATCH
    try:
        cancelled = transport.reaction_users(pending.message_id, CANCEL_EMOJI)
        approved = transport.reaction_users(pending.message_id, APPROVE_EMOJI)
    except _TRANSPORT_ERRORS as error:
        raise ApprovalSurfaceError(str(error)) from error
    if owner_reacted(cancelled, owner_id):
        return Probe.CANCELLED
    if owner_reacted(approved, owner_id):
        return Probe.APPROVED
    return Probe.BOUND_PENDING


def _ticket(key: str) -> str:
    if not key.startswith(KEY_PREFIX):
        raise ApprovalRecordsError(key)
    return key.removeprefix(KEY_PREFIX)


def request_of(
    pending: PendingRepairApproval,
    binding: ApprovalBinding | None = None,
) -> ApprovalRequest:
    """Map one stored record onto the façade's request view."""
    return ApprovalRequest(
        key=repair_approval_key(pending.ticket_id),
        action_hash=pending.action_hash,
        message_id=pending.message_id,
        channel_id=(pending.channel_id or "") if binding is None else binding.channel_id,
        created_at=pending.created_at.isoformat(),
    )


@dataclass(frozen=True, slots=True)
class RepairApprovalPayload:
    """The facts only a fresh request needs; a reused request never reads them."""

    patch_name: str
    nonce: str
    now: Callable[[], datetime]
    binding: ApprovalBinding | None = None
    patch_sha256: str | None = None
    changes: tuple[PatchFileDelta, ...] | None = None
    patch_source_path: str | None = None


@dataclass(frozen=True, slots=True)
class RepairApprovalGate:
    """Bind the repair pending store and its persisted surface to the shared lifecycle."""

    store: PendingRepairApprovalStore
    transport: ApprovalRequestTransport
    owner_id: str
    payload: RepairApprovalPayload

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        """Return this ticket's live requests, refusing rather than skipping a bad record."""
        ticket = _ticket(key)
        return tuple(
            request_of(record) for record in self.store.all_strict() if record.ticket_id == ticket
        )

    def probe(self, request: ApprovalRequest) -> Probe:
        """Classify a request; an unmatched or unreadable record is refused, never dropped."""
        record = self._record(request)
        if record is None:
            return Probe.BINDING_MISMATCH
        return probe_pending(record, self.owner_id, _transport_for_pending(record, self.transport))

    def delete(self, request: ApprovalRequest) -> None:
        """Remove a superseded request before its record may be dropped."""
        record = self._record(request)
        if record is None:
            raise ApprovalSurfaceError("repair approval record is unavailable for supersede")
        transport = _transport_for_pending(record, self.transport)
        if not isinstance(transport, ApprovalDeleteSurface):
            raise ApprovalSurfaceError("repair approval transport cannot supersede a request")
        try:
            transport.delete_message(request.message_id)
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        """Compare-and-swap: discard the record only while it still matches this request."""
        record = self._record(request)
        if record is not None:
            self.store.drop(record)

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        """Post exactly one nonce-bound request with both terminal reactions pre-added."""
        draft = self._draft(intent)
        try:
            message_id = self.transport.post_approval(approval_request_content(draft))
            self.transport.add_reaction(message_id, APPROVE_EMOJI)
            self.transport.add_reaction(message_id, CANCEL_EMOJI)
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error
        return PostedApproval(message_id=message_id, channel_id=intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        """Write the only record this ticket has; the repair clock owns its timestamp."""
        del created_at
        draft = self._draft(intent)
        self.store.save(replace(draft, message_id=posted.message_id))

    def _draft(self, intent: ApprovalIntent) -> PendingRepairApproval:
        binding = self.payload.binding
        return PendingRepairApproval(
            _ticket(intent.key),
            self.payload.patch_name,
            intent.action_hash,
            self.payload.nonce,
            "",
            self.payload.now(),
            content_binding_version=None if self.payload.patch_sha256 is None else CONTENT_BINDING_VERSION,
            patch_sha256=self.payload.patch_sha256,
            changes=self.payload.changes,
            patch_source_path=self.payload.patch_source_path,
            kind=None if binding is None else binding.kind,
            surface=None if binding is None else binding.surface,
            channel_id=None if binding is None else binding.channel_id,
            policy_version=None if binding is None else binding.policy_version,
        )

    def _record(self, request: ApprovalRequest) -> PendingRepairApproval | None:
        try:
            records = self.store.all_strict()
        except ApprovalRecordsError:
            return None
        binding = (request.action_hash, request.message_id)
        for record in records:
            if (record.action_hash, record.message_id) == binding:
                return record
        return None


def _transport_for_pending(
    pending: PendingRepairApproval,
    transport: ApprovalRequestTransport,
) -> ApprovalRequestTransport:
    """Select the record-bound transport when the implementation supports it."""
    if isinstance(transport, PendingBoundTransport):
        return transport.for_pending(pending)
    return transport
