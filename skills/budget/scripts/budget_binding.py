"""Where one budget approval lives — resolved once, then replayed.

A new request asks the shared directory for its binding exactly once; the draft
record persists that answer, and every later read, reaction poll and delete
replays the STORED binding instead of resolving again. A record written before
this schema carries no binding and drains through the legacy migrator, so the
historical approval message stays consumable and is never retargeted.

This is the only budget module that names an approval surface: the transport
(``budget_confirm``) and the record store (``budget_gate``) consume a binding
they are handed. The repo modules are reached lazily through
``AUTOPHAGY_REPO_ROOT`` (``budget_gate.repo_module``) because a deployed skill
cannot import ``automation.*`` at module scope, and an unreachable repo refuses
the request instead of falling back to an unbound surface.
"""
from __future__ import annotations

from types import ModuleType
from typing import Protocol

import budget_confirm
import budget_gate


class _Directory(Protocol):
    def owner_dm(self) -> str: ...


def _surface() -> ModuleType:
    return budget_gate.repo_module("approval_surface")


def approval_directory() -> _Directory:
    """The one approval-surface resolver, bound to THIS bot's identity (SI-7)."""
    return budget_gate.repo_module("approval_directory").DiscordChannelDirectory(
        token=budget_confirm.bot_token(),
        owner_id=budget_confirm.owner_id(),
        api=budget_confirm._api,
        cache_path=budget_gate.gate_dir() / "channel.json",
    )


def new_binding() -> budget_gate.ApprovalBindingLike:
    """Resolve the surface for a NEW post — the only surface resolution in this flow."""
    surface = _surface()
    try:
        return surface.resolve_new_binding(
            surface.ApprovalKind.BUDGET_MAIL, approval_directory(), budget_confirm.owner_id()
        )
    except surface.ApprovalSurfaceError as error:
        raise budget_gate.GateError(f"승인 표면 해석 실패 — 게시 거부: {error}", 3) from error


def stored_binding(record: dict) -> budget_gate.ApprovalBindingLike:
    """The binding this record's message lives on — read from the record, never re-resolved."""
    surface = _surface()
    kind = surface.ApprovalKind.BUDGET_MAIL
    record_kind = record.get("kind")
    if record_kind is not None and record_kind != kind.value:
        raise budget_gate.GateError(f"레코드 승인 종류가 budget이 아님 — 거부: {record_kind!r}", 1)
    channel_id, record_surface = record.get("channel_id"), record.get("surface")
    version = record.get("policy_version")
    directory, owner = approval_directory(), budget_confirm.owner_id()
    bound = isinstance(record_surface, str) and type(version) is int
    try:
        if bound:
            return surface.validate_stored_binding(
                surface.ApprovalBinding(
                    kind, surface.ApprovalSurface(record_surface), str(channel_id), version
                ),
                directory,
                owner,
            )
        legacy = channel_id if isinstance(channel_id, str) else None
        return surface.legacy_binding(kind, legacy, directory, owner)
    except (surface.ApprovalSurfaceError, ValueError) as error:
        raise budget_gate.GateError(f"저장된 승인 표면 검증 실패 — 거부: {error}", 1) from error


def persisted_channel_id(record: dict) -> str | None:
    """The channel this record was ACTUALLY posted to, read raw — no resolve, no network.

    An injected confirm only compares against where the message already lives, so it
    must never resolve a new binding: doing so both reaches Discord and would invent a
    channel for a record that was never posted.
    """
    channel_id, surface = record.get("channel_id"), record.get("surface")
    version = record.get("policy_version")
    if (
        isinstance(channel_id, str)
        and channel_id.isdigit()
        and isinstance(surface, str)
        and surface
        and type(version) is int
        and version >= 0
    ):
        return channel_id
    return None


def binding_for(record: dict) -> budget_gate.ApprovalBindingLike:
    """A stored binding always wins; only a never-posted record resolves a new one."""
    channel_id = record.get("channel_id")
    if isinstance(channel_id, str) and channel_id:
        return stored_binding(record)
    return new_binding()


def reaction_instruction(record: dict, *, name_surface: bool = False) -> str:
    """The owner-facing reaction line for THIS record's surface — never hardcoded.

    A record that already carries a binding is described by ITS stored surface; a
    draft that has not been posted yet is described by current policy.
    """
    surface_module = _surface()
    kind = surface_module.ApprovalKind.BUDGET_MAIL
    stored = record.get("surface")
    surface = (
        surface_module.ApprovalSurface(stored)
        if isinstance(stored, str)
        else surface_module.required_surface(kind)
    )
    return str(surface_module.reaction_instruction(kind, surface, name_surface=name_surface))
