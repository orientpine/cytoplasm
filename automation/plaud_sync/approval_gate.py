"""Owner approval adapter binding one plaud lifelog note to the shared lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol
from urllib.error import HTTPError

from automation.interop.approval_lease import ApprovalLease, PostingJournal
from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalRequest,
    ApprovalSurfaceError,
    PostedApproval,
    Probe,
    Verdict,
    request_owner_approval,
)
from automation.interop.approval_surface import ApprovalBinding

from .model import PlaudSyncRecord
from .render import render_plaud_approval

APPROVE_EMOJI: Final = "\u2705"
CANCEL_EMOJI: Final = "\u26d4"
_TRANSPORT_ERRORS: Final = (OSError, ValueError, KeyError, TypeError, RuntimeError)


class PlaudStoreLike(Protocol):
    def pending(self) -> tuple[PlaudSyncRecord, ...]: ...

    def set_message_id(
        self, record: PlaudSyncRecord, message_id: str, channel_id: str
    ) -> None: ...

    def clear_message_id(self, key: str, action_hash: str, message_id: str) -> None: ...


class DiscordTransportLike(Protocol):
    owner_id: str

    def post_message(self, channel_id: str, content: str) -> str: ...

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None: ...

    def get_message(self, channel_id: str, message_id: str) -> str | None: ...

    def get_reaction_users(
        self, channel_id: str, message_id: str, emoji: str
    ) -> tuple[tuple[str, bool], ...]: ...

    def delete_message(self, channel_id: str, message_id: str) -> None: ...


def _owner_reacted(users: tuple[tuple[str, bool], ...], owner_id: str) -> bool:
    return any(user_id == owner_id and not is_bot for user_id, is_bot in users)


@dataclass(frozen=True, slots=True)
class PlaudApprovalGate:
    """Adapt plaud-sync persistence and Discord I/O to ``ApprovalGate``."""

    record: PlaudSyncRecord
    store: PlaudStoreLike
    transport: DiscordTransportLike
    journal: PostingJournal | None = None
    preview: str = ""

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        return tuple(
            ApprovalRequest(
                key=key,
                action_hash=record.action_hash,
                message_id=record.message_id,
                channel_id=record.channel_id,
                created_at=record.created_at,
            )
            for record in self.store.pending()
            if record.recording_id == key and record.message_id
        )

    def probe(self, request: ApprovalRequest) -> Probe:
        content = self._content(request)
        if content is None:
            return Probe.MISSING
        if request.action_hash not in content:
            return Probe.BINDING_MISMATCH
        try:
            self.transport.add_reaction(request.channel_id, request.message_id, APPROVE_EMOJI)
            self.transport.add_reaction(request.channel_id, request.message_id, CANCEL_EMOJI)
            cancelled = self.transport.get_reaction_users(
                request.channel_id, request.message_id, CANCEL_EMOJI
            )
            approved = self.transport.get_reaction_users(
                request.channel_id, request.message_id, APPROVE_EMOJI
            )
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error
        if _owner_reacted(cancelled, self.transport.owner_id):
            return Probe.CANCELLED
        if _owner_reacted(approved, self.transport.owner_id):
            return Probe.APPROVED
        return Probe.BOUND_PENDING

    def _content(self, request: ApprovalRequest) -> str | None:
        try:
            return self.transport.get_message(request.channel_id, request.message_id)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error

    def delete(self, request: ApprovalRequest) -> None:
        try:
            self.transport.delete_message(request.channel_id, request.message_id)
        except HTTPError as error:
            if error.code != 404:
                raise ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        self.store.clear_message_id(request.key, request.action_hash, request.message_id)

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        content = render_plaud_approval(self.record, preview=self.preview)
        try:
            message_id = self.transport.post_message(intent.channel_id, content)
            if self.journal is not None:
                self.journal.enrich(
                    intent.key, intent.action_hash, message_id, intent.channel_id
                )
                self.store.set_message_id(self.record, message_id, intent.channel_id)
                self.journal.clear(intent.key)
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error
        for emoji in (APPROVE_EMOJI, CANCEL_EMOJI):
            try:
                self.transport.add_reaction(intent.channel_id, message_id, emoji)
            except _TRANSPORT_ERRORS:
                continue
        return PostedApproval(message_id, intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        del intent, created_at
        self.store.set_message_id(self.record, posted.message_id, posted.channel_id)


def request_approval(
    record: PlaudSyncRecord,
    *,
    preview: str = "",
    store: PlaudStoreLike,
    transport: DiscordTransportLike,
    binding: ApprovalBinding,
    lease: ApprovalLease,
    journal: PostingJournal,
) -> Verdict:
    gate = PlaudApprovalGate(
        record=record, store=store, transport=transport, journal=journal, preview=preview
    )
    intent = ApprovalIntent(
        key=record.recording_id,
        action_hash=record.action_hash,
        channel_id=binding.channel_id,
    )
    return request_owner_approval(intent, gate, lease, journal)
