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
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from . import patent_export_gate as gate
from . import patent_export_manifest as records
from .patent_export_manifest import Manifest

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from automation.interop.approval_surface import ApprovalBinding, ChannelDirectory


def repo_root() -> Path:
    """The checkout carrying ``automation.interop``, not the mounted-release depth guess."""
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


@dataclass(frozen=True, slots=True)
class _LiveRequest:
    """The one fact the shared reuse helper reads off an outstanding request."""

    channel_id: str


def live_requests(export_id: str) -> tuple[_LiveRequest, ...]:
    """This slug's outstanding request, read as ``PatentApprovalGate.outstanding`` reads it.

    Only a PENDING/APPROVED manifest that already names a posted message is live.
    A record that cannot be read yields nothing here — the lifecycle façade still
    refuses that request as store-unreadable, so skipping reuse never posts twice.
    """
    try:
        entry = records.load_manifest(export_id)
    except (records.ManifestError, OSError, ValueError):
        return ()
    if entry.message_id is None or entry.channel_id is None:
        return ()
    if entry.state not in (records.State.PENDING, records.State.APPROVED):
        return ()
    return (_LiveRequest(entry.channel_id),)


def new_binding(export_id: str) -> ApprovalBinding:
    """Decide, once, where a brand-new patent-export approval must be posted.

    The request gets its own thread named by ``export_id`` alone: a document title,
    file name or body excerpt must never appear in a thread name, only the id the
    manifest is keyed by.

    One approval key keeps ONE thread. This runs before the façade decides PENDING
    (same hash) or supersede (content changed), so resolving fresh every time left
    an empty thread per attempt; a LIVE request of this same slug lends its thread
    instead. A legacy DM or the shared kind thread never qualifies — the helper
    refuses them — so such a record migrates on its next post.
    """
    policy = _policy()
    try:
        directory, owner = _directory(), gate.owner_id()
        reused = policy.reuse_request_thread(
            policy.ApprovalKind.PATENT_EXPORT,
            live_requests(export_id),
            directory,
            owner,
        )
        if reused is not None:
            return reused
        return policy.resolve_new_binding(
            policy.ApprovalKind.PATENT_EXPORT,
            directory,
            owner,
            request=policy.RequestThread(title=export_id),
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
