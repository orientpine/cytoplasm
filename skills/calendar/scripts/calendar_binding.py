"""Resolve and replay calendar approval bindings through the shared directory."""
from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Protocol

import calendar_confirm
import calendar_gate
from calendar_pending import PendingConfirmStore


class ApprovalBindingLike(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def surface(self) -> str: ...

    @property
    def channel_id(self) -> str: ...

    @property
    def policy_version(self) -> int: ...


class PendingApproval(Protocol):
    kind: str | None
    surface: str | None
    channel_id: str
    dm_channel_id: str
    policy_version: int | None


class OwnerDmDirectory(Protocol):
    def owner_dm(self) -> str: ...


def repo_root() -> Path:
    """The checkout that actually carries ``automation.interop``.

    A mounted release makes ``parents[3]`` point at ``.../releases``. On
    2026-08-18 that made the approval surface refuse its own shared lifecycle.
    """
    override = os.environ.get("AUTOPHAGY_REPO_ROOT")
    if override:
        return Path(override).expanduser()
    here = Path(__file__).resolve()
    candidates = [*here.parents[2:6], Path("/srv/autophagy-agent-current"), Path("/srv/autophagy-agents")]
    for candidate in candidates:
        if (candidate / "automation" / "interop").is_dir():
            return candidate
    current = Path("/srv/autophagy-agent-current")
    return current if (current / "automation").is_dir() else Path("/srv/autophagy-agents")


def _repo_module(name: str) -> ModuleType:
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        return importlib.import_module(f"automation.interop.{name}")
    except ImportError:
        raise calendar_gate.GateError(
            f"승인 라이프사이클 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — 승인 게시 거부",
            3,
        ) from None


def _surface() -> ModuleType:
    return _repo_module("approval_surface")


def approval_directory() -> OwnerDmDirectory:
    """Build the shared directory bound to this skill's own credential (SI-7)."""
    directory = _repo_module("approval_directory")
    try:
        token = calendar_confirm.bot_token()
    except calendar_gate.GateError:
        token = None
    return directory.DiscordChannelDirectory(
        token=token,
        owner_id=calendar_confirm.owner_id(),
        api=calendar_confirm._api,
        cache_path=PendingConfirmStore().path.parent / "channel.json",
    )


def new_binding() -> ApprovalBindingLike:
    """Resolve the one binding stamped on every new calendar approval."""
    surface = _surface()
    try:
        return surface.resolve_new_binding(
            surface.ApprovalKind.CALENDAR,
            approval_directory(),
            calendar_confirm.owner_id(),
        )
    except surface.ApprovalSurfaceError as error:
        raise calendar_gate.GateError(f"승인 표면 해석 실패 — 게시 거부: {error}", 3) from error


def stored_binding(record: Mapping[str, str | int | None]) -> ApprovalBindingLike:
    """Replay a stored binding or migrate its legacy channel sentinel exactly once."""
    surface = _surface()
    kind = surface.ApprovalKind.CALENDAR
    record_kind = record.get("kind")
    if record_kind is not None and record_kind != kind.value:
        raise calendar_gate.GateError(f"레코드 승인 종류가 calendar가 아님 — 거부: {record_kind!r}", 1)
    channel_id = record.get("channel_id")
    legacy_channel = channel_id if isinstance(channel_id, str) else record.get("dm_channel_id")
    record_surface = record.get("surface")
    version = record.get("policy_version")
    directory = approval_directory()
    try:
        if isinstance(record_surface, str) and type(version) is int and isinstance(channel_id, str):
            return surface.validate_stored_binding(
                surface.ApprovalBinding(
                    kind,
                    surface.ApprovalSurface(record_surface),
                    channel_id,
                    version,
                ),
                directory,
                calendar_confirm.owner_id(),
            )
        return surface.legacy_binding(
            kind,
            legacy_channel if isinstance(legacy_channel, str) else None,
            directory,
            calendar_confirm.owner_id(),
        )
    except (surface.ApprovalSurfaceError, TypeError, ValueError) as error:
        raise calendar_gate.GateError(f"저장된 승인 표면 검증 실패 — 거부: {error}", 1) from error


def binding_for_entry(entry: PendingApproval) -> ApprovalBindingLike:
    """Return the authoritative binding for a persisted pending confirmation."""
    return stored_binding(
        {
            "channel_id": entry.channel_id,
            "dm_channel_id": entry.dm_channel_id,
            "kind": entry.kind,
            "policy_version": entry.policy_version,
            "surface": entry.surface,
        }
    )


def channel_for_entry(entry: PendingApproval) -> str:
    """Preserve an old concrete channel while migrating a legacy sentinel."""
    if entry.kind is None and entry.surface is None and entry.policy_version is None:
        if entry.channel_id != "dm":
            return entry.dm_channel_id
    return binding_for_entry(entry).channel_id


def reaction_instruction() -> str:
    """Render the shared surface-neutral owner reaction instruction."""
    surface = _surface()
    kind = surface.ApprovalKind.CALENDAR
    return str(surface.reaction_instruction(kind, surface.required_surface(kind)))
