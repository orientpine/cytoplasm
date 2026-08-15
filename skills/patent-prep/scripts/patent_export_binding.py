"""Where one patent-export approval lives — resolved once, then replayed.

A new request asks the shared directory for its binding exactly once; the
manifest persists that answer, and every later read, reaction poll and delete
replays the STORED binding instead of resolving again. A manifest written before
this schema carries no binding and drains through the legacy migrator, so the
historical approval message stays consumable and is never retargeted.

The repo modules are reached lazily through ``AUTOPHAGY_REPO_ROOT``: a deployed
skill cannot import ``automation.*`` at module scope, and an unreachable repo
refuses the request rather than falling back to an unbound surface.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from . import patent_export_gate as gate
from .patent_export_manifest import Manifest

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from automation.interop.approval_surface import ApprovalBinding, ChannelDirectory


def repo_root() -> Path:
    default = Path(__file__).resolve().parents[3]
    return Path(os.environ.get("AUTOPHAGY_REPO_ROOT", str(default))).expanduser()


def repo_module(name: str) -> ModuleType:
    """One shared ``automation.interop`` import seam that refuses instead of guessing."""
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        return importlib.import_module(f"automation.interop.{name}")
    except ImportError:
        raise gate.ExportGateError(
            f"approval lifecycle unavailable (AUTOPHAGY_REPO_ROOT={root}); request refused",
            3,
        ) from None


def _policy() -> ModuleType:
    return repo_module("approval_surface")


def _directory() -> ChannelDirectory:
    """The one resolver allowed to answer 'which channel', bound to this bot's identity."""
    module = repo_module("approval_directory")
    return module.DiscordChannelDirectory(
        token=gate.bot_token(),
        owner_id=gate.owner_id(),
        api=gate.discord_request,
    )


def _refuse(error: Exception) -> gate.ExportGateError:
    return gate.ExportGateError(f"patent approval surface refused: {error}", 3)


def new_binding() -> ApprovalBinding:
    """Decide, once, where a brand-new patent-export approval must be posted."""
    policy = _policy()
    try:
        return policy.resolve_new_binding(
            policy.ApprovalKind.PATENT_EXPORT,
            _directory(),
            gate.owner_id(),
        )
    except policy.ApprovalSurfaceError as error:
        raise _refuse(error) from error


def stored_binding(entry: Manifest) -> ApprovalBinding:
    """Replay the binding a manifest already holds; a pre-schema row drains as legacy."""
    policy = _policy()
    kind = policy.ApprovalKind.PATENT_EXPORT
    try:
        directory, owner = _directory(), gate.owner_id()
        if not entry.is_bound:
            return policy.legacy_binding(kind, entry.channel_id, directory, owner)
        binding = policy.ApprovalBinding(
            kind,
            policy.ApprovalSurface(entry.surface),
            entry.channel_id,
            entry.policy_version,
        )
        return policy.validate_stored_binding(binding, directory, owner)
    except (policy.ApprovalSurfaceError, ValueError) as error:
        raise _refuse(error) from error


def owner_dm() -> str:
    """The DM channel this bot opened with the owner — result notices only, never a gate."""
    policy = _policy()
    try:
        return _directory().owner_dm()
    except policy.ApprovalSurfaceError as error:
        raise _refuse(error) from error


def reaction_instruction(entry: Manifest) -> str:
    """The owner-facing reaction line for THIS record's surface — never hardcoded.

    A bound manifest is described by ITS stored surface so a request posted before
    a policy flip keeps the wording it was posted with; an unbound one falls back
    to current policy. Surface-neutral by design: the approval message itself never
    names where it lives, only how to answer it.
    """
    policy = _policy()
    kind = policy.ApprovalKind.PATENT_EXPORT
    surface = (
        policy.ApprovalSurface(entry.surface)
        if entry.surface is not None
        else policy.required_surface(kind)
    )
    return str(policy.reaction_instruction(kind, surface))
