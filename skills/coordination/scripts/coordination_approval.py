from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, TypeAlias, assert_never
from urllib.error import HTTPError, URLError
from urllib.parse import quote

import coordinate_io as io
import coordination_binding
from coordination_pending import PendingConfirm, PendingConfirmError, PendingConfirmStore

if TYPE_CHECKING:
    from automation.interop.approval_lifecycle import (
        ApprovalIntent,
        ApprovalRequest,
        PostedApproval,
        Probe,
        Verdict,
    )
APPROVE_EMOJI, CANCEL_EMOJI = "\u2705", "\u26d4"
DraftRecord: TypeAlias = dict[str, str | list[str]]
_TRANSPORT_ERRORS = (
    io.CoordinationError,
    URLError,
    OSError,
    json.JSONDecodeError,
    KeyError,
    TypeError,
)

lifecycle = coordination_binding.lifecycle
confirm_lease = coordination_binding.confirm_lease
posting_journal = coordination_binding.posting_journal


class ApprovalPollSurface(Protocol):
    def message_content(self, entry: PendingConfirm) -> str | None: ...

    def reaction_users(
        self, entry: PendingConfirm, emoji: str
    ) -> tuple[Mapping[str, str | bool], ...]: ...


def approval_key(slot: str) -> str:
    if not slot:
        raise io.CoordinationError("조율 slot 누락 — 승인 키 생성 거부", 3)
    return f"coord:{slot}"


@dataclass(frozen=True, slots=True)
class CoordinationApprovalPayload:
    draft: DraftRecord
    slot: str
    summary: str
    correlation: str
    duration_min: int
    content: str


def confirm_intent(
    payload: CoordinationApprovalPayload,
    binding: coordination_binding.ApprovalBindingLike | None = None,
) -> ApprovalIntent:
    digest = payload.draft.get("sha256")
    if not isinstance(digest, str) or not digest:
        raise io.CoordinationError("드래프트 sha256 누락 — 승인 게시 거부", 3)
    resolved = binding or coordination_binding.new_binding()
    return lifecycle().ApprovalIntent(
        key=approval_key(payload.slot), action_hash=digest, channel_id=resolved.channel_id
    )


def request_of(entry: PendingConfirm) -> ApprovalRequest:
    return lifecycle().ApprovalRequest(
        key=entry.key,
        action_hash=entry.sha256,
        message_id=entry.dm_message_id,
        channel_id=coordination_binding.channel_for_entry(entry),
        created_at=entry.created.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def probe_entry(entry: PendingConfirm, owner_id: str, surface: ApprovalPollSurface) -> Probe:
    state = lifecycle().Probe
    content = surface.message_content(entry)
    if content is None:
        return state.MISSING
    if f"sha256:{entry.sha256}" not in content:
        return state.BINDING_MISMATCH
    cancelled = surface.reaction_users(entry, CANCEL_EMOJI)
    approved = surface.reaction_users(entry, APPROVE_EMOJI)
    watch = importlib.import_module("confirm_reaction_watch")
    if watch._owner_reacted(cancelled, owner_id):
        return state.CANCELLED
    if watch._owner_reacted(approved, owner_id):
        return state.APPROVED
    return state.BOUND_PENDING


@dataclass(frozen=True, slots=True)
class CoordinationDiscord:
    owner_id: str

    def message_content(self, entry: PendingConfirm) -> str | None:
        try:
            message = io.api(
                "GET", f"/channels/{entry.dm_channel_id}/messages/{entry.dm_message_id}"
            )
        except HTTPError as error:
            if error.code == 404:
                return None
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise lifecycle().ApprovalSurfaceError("confirmation DM response is invalid")
        return str(message["content"])

    def reaction_users(
        self, entry: PendingConfirm, emoji: str
    ) -> tuple[Mapping[str, str | bool], ...]:
        endpoint = (
            f"/channels/{entry.dm_channel_id}/messages/{entry.dm_message_id}"
            f"/reactions/{quote(emoji, safe='')}?limit=100"
        )
        try:
            users = io.api("GET", endpoint)
        except HTTPError as error:
            if error.code == 404:
                return ()
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        if not isinstance(users, list):
            raise lifecycle().ApprovalSurfaceError("reaction response is invalid")
        return tuple(user for user in users if isinstance(user, dict))

    def delete(self, entry: PendingConfirm) -> None:
        try:
            io.api("DELETE", f"/channels/{entry.dm_channel_id}/messages/{entry.dm_message_id}")
        except HTTPError as error:
            if error.code != 404:
                raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error


@dataclass(frozen=True, slots=True)
class CoordinationApprovalGate:
    payload: CoordinationApprovalPayload | None
    store: PendingConfirmStore
    owner_id: str
    binding: coordination_binding.ApprovalBindingLike | None = None

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        try:
            return tuple(request_of(entry) for entry in self.store.load() if entry.key == key)
        except PendingConfirmError as error:
            raise lifecycle().ApprovalRecordsError(str(self.store.path)) from error

    def probe(self, request: ApprovalRequest) -> Probe:
        entry = self._entry(request)
        if entry is None:
            return lifecycle().Probe.BINDING_MISMATCH
        return probe_entry(entry, self.owner_id, CoordinationDiscord(self.owner_id))

    def delete(self, request: ApprovalRequest) -> None:
        entry = self._entry(request)
        if entry is None:
            raise lifecycle().ApprovalSurfaceError("pending confirmation binding changed")
        CoordinationDiscord(self.owner_id).delete(entry)

    def drop(self, request: ApprovalRequest) -> None:
        entry = self._entry(request)
        if entry is not None:
            self.store.drop(entry)

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        if self.payload is None:
            raise lifecycle().ApprovalSurfaceError("coordination approval payload unavailable")
        try:
            channel_id = intent.channel_id
            message_id = io.post_message(channel_id, self.payload.content)
            io.add_reaction(channel_id, message_id, APPROVE_EMOJI)
            io.add_reaction(channel_id, message_id, CANCEL_EMOJI)
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        return lifecycle().PostedApproval(message_id=message_id, channel_id=channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        del created_at
        if self.payload is None:
            raise lifecycle().ApprovalRecordsError("coordination approval payload unavailable")
        binding = self._binding_for(intent)
        if posted.channel_id != binding.channel_id:
            raise lifecycle().ApprovalRecordsError("coordination approval channel binding changed")
        draft = self.payload.draft
        self.store.append(
            PendingConfirm(
                draft_id=str(draft["id"]),
                sha256=intent.action_hash,
                dm_channel_id=binding.channel_id,
                dm_message_id=posted.message_id,
                slot=self.payload.slot,
                summary=self.payload.summary,
                correlation=self.payload.correlation,
                duration_min=self.payload.duration_min,
                created=_draft_created(str(draft["created"])),
                key=intent.key,
                kind=str(binding.kind),
                surface=str(binding.surface),
                channel_id=binding.channel_id,
                policy_version=binding.policy_version,
            )
        )

    def _binding_for(self, intent: ApprovalIntent) -> coordination_binding.ApprovalBindingLike:
        binding = self.binding or coordination_binding.new_binding()
        if intent.channel_id != binding.channel_id:
            raise lifecycle().ApprovalSurfaceError("coordination approval intent binding changed")
        return binding

    def _entry(self, request: ApprovalRequest) -> PendingConfirm | None:
        try:
            matches = tuple(entry for entry in self.store.load() if request_of(entry) == request)
        except PendingConfirmError as error:
            raise lifecycle().ApprovalRecordsError(str(self.store.path)) from error
        return matches[0] if len(matches) == 1 else None


def request_confirmation(payload: CoordinationApprovalPayload, owner_id: str) -> PendingConfirm:
    facade = lifecycle()
    store = PendingConfirmStore()
    binding = coordination_binding.new_binding(owner_id)
    verdict = facade.request_owner_approval(
        confirm_intent(payload, binding),
        CoordinationApprovalGate(payload, store, owner_id, binding),
        confirm_lease(),
        posting_journal(),
    )
    return _entry_from_verdict(verdict, store)


def _entry_from_verdict(verdict: Verdict, store: PendingConfirmStore) -> PendingConfirm:
    outcome = lifecycle().Outcome
    match verdict.outcome:
        case outcome.POSTED:
            posted = verdict.posted
            request = None if posted is None else next(
                (request_of(entry) for entry in store.load() if entry.dm_message_id == posted.message_id),
                None,
            )
        case outcome.PENDING:
            request = verdict.live
        case outcome.DEFERRED | outcome.REFUSED:
            reason = verdict.reason.value if verdict.reason is not None else "unknown"
            exit_code = 3 if reason in {"store-unreadable", "posting-journal-stale"} else 1
            raise io.CoordinationError(
                f"승인 메시지를 게시하지 않았습니다 ({verdict.outcome.value}:{reason})",
                exit_code,
            )
        case unreachable:
            assert_never(unreachable)
    if request is None:
        raise io.CoordinationError("승인 게시 결과가 비어 있습니다", 3)
    entry = CoordinationApprovalGate(None, store, "")._entry(request)
    if entry is None:
        raise io.CoordinationError("pending confirm 결과를 찾을 수 없습니다", 3)
    return entry


def _draft_created(value: str) -> datetime:
    created = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise io.CoordinationError("드래프트 created UTC 누락", 3)
    return created.astimezone(UTC)
