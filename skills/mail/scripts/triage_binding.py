"""Lazy mail approval-binding adapter."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType

import triage_confirm
import triage_gate


def repo_root() -> Path:
    """The checkout that actually carries ``automation.interop``.

    See `triage_approval.repo_root` — identical mounted-release depth-guess trap.
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
        raise triage_gate.GateError(
            f"승인 라이프사이클 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — 승인 게시 거부", 3
        ) from None


def approval_directory() -> object:
    directory_module = _repo_module("approval_directory")
    try:
        token = triage_confirm.bot_token()
    except triage_gate.GateError:
        token = None
    return directory_module.DiscordChannelDirectory(
        token=token,
        owner_id=triage_confirm.owner_id(),
        api=triage_confirm._api,
    )


def approval_kind(draft: dict) -> object:
    surface_module = _repo_module("approval_surface")
    match draft.get("kind"):
        case "compose":
            return surface_module.ApprovalKind.MAIL_COMPOSE
        case "reply" | None:
            return surface_module.ApprovalKind.MAIL_REPLY
        case unsupported:
            raise triage_gate.GateError(
                f"지원하지 않는 메일 승인 kind: {unsupported!r}", 3,
            )


def draft_kind(draft: dict) -> str:
    return "compose" if draft.get("kind") == "compose" else "reply"


def persisted_channel_id(draft: dict) -> str | None:
    channel_id = draft.get("channel_id")
    surface = draft.get("surface")
    policy_version = draft.get("policy_version")
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


def stored_binding(draft: dict) -> object:
    surface_module = _repo_module("approval_surface")
    kind = approval_kind(draft)
    channel_id = draft.get("channel_id")
    surface = draft.get("surface")
    policy_version = draft.get("policy_version")
    directory = approval_directory()
    try:
        if isinstance(surface, str) or policy_version is not None:
            if (
                not isinstance(surface, str)
                or not isinstance(channel_id, str)
                or not isinstance(policy_version, int)
            ):
                raise triage_gate.GateError("저장된 승인 바인딩이 불완전함 — 승인 거부", 3)
            binding = surface_module.ApprovalBinding(
                kind,
                surface_module.ApprovalSurface(surface),
                channel_id,
                policy_version,
            )
            return surface_module.validate_stored_binding(
                binding,
                directory,
                triage_confirm.owner_id(),
            )
        if channel_id not in (None, "") or draft.get("kind") is None:
            return surface_module.legacy_binding(
                kind,
                channel_id if isinstance(channel_id, str) else None,
                directory,
                triage_confirm.owner_id(),
            )
        return surface_module.resolve_new_binding(kind, directory, triage_confirm.owner_id())
    except (RuntimeError, TypeError, ValueError) as error:
        if isinstance(error, triage_gate.GateError):
            raise
        raise triage_gate.GateError(f"승인 바인딩을 확인할 수 없음 — {error}", 3) from error


def is_retired_binding(draft: dict) -> bool:
    """Whether persisted metadata names a surface no longer used for its kind."""
    surface_module = _repo_module("approval_surface")
    kind = approval_kind(draft)
    raw_surface = draft.get("surface")
    policy_version = draft.get("policy_version")
    if isinstance(raw_surface, str) and type(policy_version) is int:
        surface = surface_module.ApprovalSurface(raw_surface)
        stamped = surface_module.surface_at_policy(kind, policy_version)
        if surface is not stamped:
            raise triage_gate.GateError("저장된 승인 표면과 정책 버전이 모순됨 — 거부", 3)
        return surface is not surface_module.required_surface(kind)
    if raw_surface is not None or policy_version is not None:
        raise triage_gate.GateError("저장된 승인 바인딩이 불완전함 — 승인 거부", 3)
    if draft.get("kind") is None or persisted_channel_id(draft) is not None:
        return surface_module.surface_at_policy(kind, 0) is not surface_module.required_surface(kind)
    return False


def reaction_instruction(draft: dict, *, name_surface: bool = False) -> str:
    surface_module = _repo_module("approval_surface")
    kind = approval_kind(draft)
    raw_surface = draft.get("surface")
    surface = (
        surface_module.ApprovalSurface(raw_surface)
        if isinstance(raw_surface, str)
        else surface_module.required_surface(kind)
    )
    return str(surface_module.reaction_instruction(kind, surface, name_surface=name_surface))
