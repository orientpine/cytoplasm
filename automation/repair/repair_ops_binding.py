"""Shared approval-binding adapter for the ops repair bot."""
from __future__ import annotations

from typing import Protocol

from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.approval_surface import (
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    ChannelDirectory,
    ApprovalSurfaceError,
    legacy_binding,
    resolve_new_binding,
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


def new_binding(directory: ChannelDirectory, owner_id: str) -> ApprovalBinding:
    """Resolve the one binding stamped on a newly posted repair request."""
    return resolve_new_binding(ApprovalKind.REPAIR, directory, owner_id)


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
