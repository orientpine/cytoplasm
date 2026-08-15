"""Owner-DM approval adapter for one memory relocation record."""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Final, Protocol, TypeGuard
from urllib.error import HTTPError

from .model import RelocationRecord, record_key
from .render import render_relocation_approval

if TYPE_CHECKING:
    from automation.interop.approval_lease import ApprovalLease, PostingJournal
    from automation.interop.approval_lifecycle import (
        ApprovalIntent,
        ApprovalGate,
        ApprovalRequest,
        ApprovalSurfaceError,
        PostedApproval,
        Probe,
        Verdict,
    )
    from automation.interop.approval_surface import ApprovalBinding

APPROVE_EMOJI: Final = "\u2705"
CANCEL_EMOJI: Final = "\u26d4"
_TRANSPORT_ERRORS: Final = (OSError, ValueError, KeyError, TypeError)
_LIFECYCLE_API: Final = (
    "ApprovalIntent",
    "ApprovalRequest",
    "ApprovalSurfaceError",
    "PostedApproval",
    "Probe",
    "request_owner_approval",
)


class RelocationStoreLike(Protocol):
    """Persistent operations required by the shared approval lifecycle."""

    def pending(self) -> tuple[RelocationRecord, ...]: ...

    def set_message_id(
        self, record: RelocationRecord, message_id: str, channel_id: str
    ) -> None: ...

    def clear_message_id(self, key: str, action_hash: str, message_id: str) -> None: ...


class DiscordTransportLike(Protocol):
    """Discord operations injected at the owner-approval boundary."""

    owner_id: str

    def post_message(self, channel_id: str, content: str) -> str: ...

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None: ...

    def get_message(self, channel_id: str, message_id: str) -> str | None: ...

    def get_reaction_users(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> tuple[tuple[str, bool], ...]: ...

    def delete_message(self, channel_id: str, message_id: str) -> None: ...


class LifecycleModule(Protocol):
    """Typed view of the lazily imported shared lifecycle module."""

    ApprovalIntent: type[ApprovalIntent]
    ApprovalRequest: type[ApprovalRequest]
    ApprovalSurfaceError: type[ApprovalSurfaceError]
    PostedApproval: type[PostedApproval]
    Probe: type[Probe]

    def request_owner_approval(
        self,
        intent: ApprovalIntent,
        gate: ApprovalGate,
        lease: ApprovalLease,
        journal: PostingJournal,
    ) -> Verdict: ...


def repo_root() -> Path:
    default = Path(__file__).resolve().parents[2]
    return Path(os.environ.get("AUTOPHAGY_REPO_ROOT", str(default))).expanduser()


def _repo_module(name: str) -> ModuleType:
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module(f"automation.interop.{name}")


def _is_lifecycle_module(module: ModuleType) -> TypeGuard[LifecycleModule]:
    return all(hasattr(module, name) for name in _LIFECYCLE_API)


def lifecycle() -> LifecycleModule:
    """Load the deployed repository's shared approval lifecycle."""
    module = _repo_module("approval_lifecycle")
    if not _is_lifecycle_module(module):
        raise ModuleNotFoundError("shared approval lifecycle has an invalid API")
    return module


def _owner_reacted(users: tuple[tuple[str, bool], ...], owner_id: str) -> bool:
    return any(user_id == owner_id and not is_bot for user_id, is_bot in users)


@dataclass(frozen=True, slots=True)
class RelocateApprovalGate:
    """Adapt relocation persistence and Discord I/O to ``ApprovalGate``."""

    record: RelocationRecord
    entry_text: str
    store: RelocationStoreLike
    transport: DiscordTransportLike

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        request_type = lifecycle().ApprovalRequest
        return tuple(
            request_type(
                key=key,
                action_hash=record.action_hash,
                message_id=record.message_id,
                channel_id=record.channel_id,
                created_at=record.created_at,
            )
            for record in self.store.pending()
            if record_key(record.source_kind, record.entry_sha256) == key
            and record.message_id
        )

    def probe(self, request: ApprovalRequest) -> Probe:
        state = lifecycle().Probe
        content = self._content(request)
        if content is None:
            return state.MISSING
        if request.action_hash not in content:
            return state.BINDING_MISMATCH
        try:
            cancelled = self.transport.get_reaction_users(
                request.channel_id,
                request.message_id,
                CANCEL_EMOJI,
            )
            approved = self.transport.get_reaction_users(
                request.channel_id,
                request.message_id,
                APPROVE_EMOJI,
            )
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        if _owner_reacted(cancelled, self.transport.owner_id):
            return state.CANCELLED
        if _owner_reacted(approved, self.transport.owner_id):
            return state.APPROVED
        return state.BOUND_PENDING

    def _content(self, request: ApprovalRequest) -> str | None:
        try:
            return self.transport.get_message(request.channel_id, request.message_id)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error

    def delete(self, request: ApprovalRequest) -> None:
        try:
            self.transport.delete_message(request.channel_id, request.message_id)
        except HTTPError as error:
            if error.code != 404:
                raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        """Request a compare-and-swap unbind of the exact stored binding."""
        self.store.clear_message_id(
            request.key,
            request.action_hash,
            request.message_id,
        )

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        content = render_relocation_approval(self.record, entry_text=self.entry_text)
        try:
            message_id = self.transport.post_message(intent.channel_id, content)
            self.transport.add_reaction(intent.channel_id, message_id, APPROVE_EMOJI)
            self.transport.add_reaction(intent.channel_id, message_id, CANCEL_EMOJI)
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        return lifecycle().PostedApproval(message_id, intent.channel_id)

    def commit(
        self,
        intent: ApprovalIntent,
        posted: PostedApproval,
        created_at: str,
    ) -> None:
        """Bind the message id AND its channel through the store's no-overwrite operation."""
        del intent, created_at
        self.store.set_message_id(self.record, posted.message_id, posted.channel_id)


def request_approval(
    record: RelocationRecord,
    entry_text: str,
    *,
    store: RelocationStoreLike,
    transport: DiscordTransportLike,
    binding: ApprovalBinding,
    lease: ApprovalLease,
    journal: PostingJournal,
) -> Verdict:
    """Request one lifecycle-managed owner approval on the injected binding."""
    shared = lifecycle()
    intent = shared.ApprovalIntent(
        key=record_key(record.source_kind, record.entry_sha256),
        action_hash=record.action_hash,
        channel_id=binding.channel_id,
    )
    return shared.request_owner_approval(
        intent,
        RelocateApprovalGate(record, entry_text, store, transport),
        lease,
        journal,
    )
