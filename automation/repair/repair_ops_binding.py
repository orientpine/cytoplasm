"""Shared approval-binding adapter for the ops repair bot."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.approval_surface import (
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    ChannelDirectory,
    ApprovalSurfaceError,
    LiveRequest,
    RequestThread,
    legacy_binding,
    resolve_new_binding,
    reuse_request_thread,
    validate_stored_binding,
)


class RepairBindingRecord(Protocol):
    """Stored repair fields required to restore an approval binding."""

    kind: ApprovalKind | None
    surface: ApprovalSurface | None
    channel_id: str | None
    policy_version: int | None


def directory_for_ops(token: str, owner_id: str) -> DiscordChannelDirectory:
    """Construct the directory with the repair bot's own credential."""
    return DiscordChannelDirectory(
        token=token,
        owner_id=owner_id,
    )


def new_binding(
    directory: ChannelDirectory,
    owner_id: str,
    ticket_id: str | None = None,
    outstanding: Iterable[LiveRequest] = (),
) -> ApprovalBinding:
    """Resolve the one binding stamped on a newly posted repair request.

    A ticket id opens that request's OWN thread, titled by the ticket alone — a
    repair has no owner instruction to anchor to, and a diagnosis line is never a
    title. Approval, reminders and the owner's decision then live in one place per
    ticket instead of one shared queue for all of them.

    ``outstanding`` is this ticket's live pending record(s). One ticket keeps ONE
    thread: this resolves before the façade decides PENDING (same patch) or
    supersede (patch changed), so without the reuse an empty thread was left behind
    on every re-request. A legacy DM record never qualifies — the shared helper
    refuses it — so such a ticket migrates on its next post.

    ``None`` is the read-only bootstrap the watcher and the apply command take:
    both rebind to the channel their stored record already names, and the
    directory OPENS a thread per call, so asking for one here would leave an empty
    second thread behind for a ticket that already has its own.
    """
    request = None if ticket_id is None else RequestThread(title=ticket_id)
    if request is not None:
        reused = reuse_request_thread(ApprovalKind.REPAIR, outstanding, directory, owner_id)
        if reused is not None:
            return reused
    return resolve_new_binding(ApprovalKind.REPAIR, directory, owner_id, request=request)


def stored_binding(
    record: RepairBindingRecord,
    directory: ChannelDirectory,
    owner_id: str,
) -> ApprovalBinding:
    """Validate a full stored binding or migrate an entirely legacy repair record."""
    match record.kind, record.surface, record.channel_id, record.policy_version:
        case None, None, None, None:
            return legacy_binding(ApprovalKind.REPAIR, None, directory, owner_id)
        case ApprovalKind.REPAIR, ApprovalSurface() as surface, str() as channel_id, int() as policy_version:
            binding = ApprovalBinding(ApprovalKind.REPAIR, surface, channel_id, policy_version)
            return validate_stored_binding(binding, directory, owner_id)
        case _:
            raise ApprovalSurfaceError("stored repair approval binding is incomplete")
