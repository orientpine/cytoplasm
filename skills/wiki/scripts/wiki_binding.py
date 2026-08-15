"""Resolve and replay wiki approval bindings through the shared directory."""
from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Protocol

import wiki_gate


class ApprovalBindingLike(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def surface(self) -> str: ...

    @property
    def channel_id(self) -> str: ...

    @property
    def policy_version(self) -> int: ...


class OwnerDmDirectory(Protocol):
    def owner_dm(self) -> str: ...


def _repo_module(name: str) -> ModuleType:
    root = Path(
        os.environ.get("AUTOPHAGY_REPO_ROOT", str(Path(__file__).resolve().parents[3]))
    ).expanduser()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        return importlib.import_module(f"automation.interop.{name}")
    except ImportError:
        raise wiki_gate.GateError(
            f"승인 라이프사이클 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — 승인 게시 거부",
            3,
        ) from None


def _surface() -> ModuleType:
    return _repo_module("approval_surface")


def _bot_token() -> str | None:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    return token or None


def approval_directory() -> OwnerDmDirectory:
    """Build the shared directory bound to this skill's own credential (SI-7)."""
    directory = _repo_module("approval_directory")
    return directory.DiscordChannelDirectory(
        token=_bot_token(),
        owner_id=wiki_gate.owner_id(),
        api=wiki_gate._api,
        cache_path=wiki_gate.GATE_DIR / "channel.json",
    )


def new_binding() -> ApprovalBindingLike:
    """Resolve the one binding stamped on every new wiki approval."""
    surface = _surface()
    try:
        return surface.resolve_new_binding(
            surface.ApprovalKind.WIKI,
            approval_directory(),
            wiki_gate.owner_id(),
        )
    except surface.ApprovalSurfaceError as error:
        raise wiki_gate.GateError(f"승인 표면 해석 실패 — 게시 거부: {error}", 3) from error


def stored_binding(record: Mapping[str, str | int | None]) -> ApprovalBindingLike:
    """Replay a stored binding or migrate its legacy channel sentinel exactly once."""
    has_bound_message = any(
        isinstance(record.get(name), str) and record[name]
        for name in ("confirm_message_id", "message_id")
    )
    if has_bound_message and persisted_channel_id(record) is None:
        draft_id = record.get("id")
        if isinstance(draft_id, str):
            record = wiki_gate.load_draft(draft_id)

    surface_module = _surface()
    kind = surface_module.ApprovalKind.WIKI
    record_kind = record.get("kind")
    if record_kind is not None and record_kind != kind.value:
        raise wiki_gate.GateError(f"레코드 승인 종류가 wiki가 아님 — 거부: {record_kind!r}", 1)
    channel_id = record.get("channel_id")
    record_surface = record.get("surface")
    version = record.get("policy_version")
    persisted_channel = persisted_channel_id(record)
    if has_bound_message:
        if persisted_channel is None:
            raise wiki_gate.GateError("저장된 승인 바인딩이 불완전함 — 승인 거부", 1)
        try:
            return surface_module.ApprovalBinding(
                kind,
                surface_module.ApprovalSurface(record_surface),
                persisted_channel,
                version,
            )
        except (surface_module.ApprovalSurfaceError, TypeError, ValueError) as error:
            raise wiki_gate.GateError(f"저장된 승인 표면 검증 실패 — 거부: {error}", 1) from error

    directory = approval_directory()
    try:
        if isinstance(record_surface, str) and type(version) is int and isinstance(channel_id, str):
            return surface_module.validate_stored_binding(
                surface_module.ApprovalBinding(
                    kind,
                    surface_module.ApprovalSurface(record_surface),
                    channel_id,
                    version,
                ),
                directory,
                wiki_gate.owner_id(),
            )
        return surface_module.legacy_binding(
            kind,
            channel_id if isinstance(channel_id, str) else None,
            directory,
            wiki_gate.owner_id(),
        )
    except (surface_module.ApprovalSurfaceError, TypeError, ValueError) as error:
        raise wiki_gate.GateError(f"저장된 승인 표면 검증 실패 — 거부: {error}", 1) from error


def persisted_channel_id(record: Mapping[str, str | int | None]) -> str | None:
    """Read a complete persisted binding's channel without resolving or fact-checking it."""
    channel_id = record.get("channel_id")
    surface = record.get("surface")
    policy_version = record.get("policy_version")
    if (
        isinstance(channel_id, str)
        and channel_id.isdigit()
        and isinstance(surface, str)
        and surface
        and type(policy_version) is int
        and policy_version >= 0
    ):
        return channel_id
    return None


def reaction_instruction() -> str:
    """Render the shared surface-neutral owner reaction instruction."""
    surface = _surface()
    kind = surface.ApprovalKind.WIKI
    return str(surface.reaction_instruction(kind, surface.required_surface(kind)))
