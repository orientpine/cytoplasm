from __future__ import annotations

import importlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Protocol, TypeAlias, assert_never
from urllib.error import HTTPError, URLError

import calendar_confirm
import calendar_gate
import calendar_binding
from calendar_pending import PendingConfirm, PendingConfirmError, PendingConfirmStore

if TYPE_CHECKING:
    from automation.interop.approval_lifecycle import (
        ApprovalIntent,
        ApprovalRequest,
        PostedApproval,
        Probe,
        Verdict,
    )
    from automation.interop.approval_lease import ApprovalLease, PostingJournal

LEASE_DIRNAME = "approval-leases"
JOURNAL_DIRNAME = "posting-journal"
DraftRecord: TypeAlias = dict[str, str | list[str]]
_TRANSPORT_ERRORS = (
    calendar_gate.GateError,
    URLError,
    OSError,
    json.JSONDecodeError,
    KeyError,
    TypeError,
)


class ApprovalPollSurface(Protocol):
    def message_content(self, entry: PendingConfirm) -> str | None: ...

    def reaction_users(
        self, entry: PendingConfirm, emoji: str
    ) -> tuple[dict[str, str | bool], ...]: ...


def repo_root() -> Path:
    default = Path(__file__).resolve().parents[3]
    return Path(os.environ.get("AUTOPHAGY_REPO_ROOT", str(default))).expanduser()


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


def lifecycle() -> ModuleType:
    return _repo_module("approval_lifecycle")


def _lease_module() -> ModuleType:
    return _repo_module("approval_lease")


def approval_key(draft: DraftRecord) -> str:
    calendar_id = draft.get("calendar_id")
    event_id = draft.get("event_id")
    start = draft.get("start")
    if not isinstance(calendar_id, str) or not calendar_id:
        raise calendar_gate.GateError("드래프트 calendar_id 누락 — 승인 키 생성 거부", 3)
    subject = event_id if isinstance(event_id, str) and event_id else start
    if not isinstance(subject, str) or not subject:
        raise calendar_gate.GateError("드래프트 event_id/start 누락 — 승인 키 생성 거부", 3)
    return f"calendar:{calendar_id}:{subject}"


def confirm_intent(
    draft: DraftRecord, binding: calendar_binding.ApprovalBindingLike | None = None
) -> ApprovalIntent:
    digest = draft.get("sha256")
    if not isinstance(digest, str) or not digest:
        raise calendar_gate.GateError("드래프트 sha256 누락 — 승인 게시 거부", 3)
    resolved = binding or calendar_binding.new_binding()
    return lifecycle().ApprovalIntent(
        key=approval_key(draft), action_hash=digest, channel_id=resolved.channel_id
    )


def confirm_lease(state_dir: Path | None = None) -> ApprovalLease:
    root = state_dir or PendingConfirmStore().path.parent
    return _lease_module().FileKeyLease(root / LEASE_DIRNAME)


def posting_journal() -> PostingJournal:
    return _lease_module().PostingJournal(PendingConfirmStore().path.parent / JOURNAL_DIRNAME)


def request_of(entry: PendingConfirm) -> ApprovalRequest:
    return lifecycle().ApprovalRequest(
        key=entry.key,
        action_hash=entry.sha256,
        message_id=entry.dm_message_id,
        channel_id=calendar_binding.channel_for_entry(entry),
        created_at=entry.created.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def probe_entry(entry: PendingConfirm, owner_id: str, surface: ApprovalPollSurface) -> Probe:
    state = lifecycle().Probe
    content = surface.message_content(entry)
    if content is None:
        return state.MISSING
    if f"sha256:{entry.sha256}" not in content:
        return state.BINDING_MISMATCH
    cancelled = surface.reaction_users(entry, calendar_confirm.CANCEL_EMOJI)
    approved = surface.reaction_users(entry, calendar_confirm.APPROVE_EMOJI)
    if calendar_confirm._owner_reacted(cancelled, owner_id):
        return state.CANCELLED
    if calendar_confirm._owner_reacted(approved, owner_id):
        return state.APPROVED
    return state.BOUND_PENDING


@dataclass(frozen=True, slots=True)
class CalendarDiscord:
    def message_content(self, entry: PendingConfirm) -> str | None:
        try:
            return calendar_confirm.confirmation_message_content(entry)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error

    def reaction_users(
        self, entry: PendingConfirm, emoji: str
    ) -> tuple[dict[str, str | bool], ...]:
        try:
            return calendar_confirm.confirmation_reaction_users(entry, emoji)
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error

    def delete(self, entry: PendingConfirm) -> None:
        try:
            calendar_confirm._api(
                "DELETE", f"/channels/{entry.dm_channel_id}/messages/{entry.dm_message_id}"
            )
        except HTTPError as error:
            if error.code != 404:
                raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error


@dataclass(frozen=True, slots=True)
class CalendarApprovalGate:
    draft: DraftRecord | None
    store: PendingConfirmStore
    owner_id: str
    binding: calendar_binding.ApprovalBindingLike | None = None

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        try:
            return tuple(request_of(entry) for entry in self.store.load() if entry.key == key)
        except PendingConfirmError as error:
            raise lifecycle().ApprovalRecordsError(str(self.store.path)) from error

    def probe(self, request: ApprovalRequest) -> Probe:
        entry = self._entry(request)
        if entry is None:
            return lifecycle().Probe.BINDING_MISMATCH
        return probe_entry(entry, self.owner_id, CalendarDiscord())

    def delete(self, request: ApprovalRequest) -> None:
        entry = self._entry(request)
        if entry is None:
            raise lifecycle().ApprovalSurfaceError("pending confirmation binding changed")
        CalendarDiscord().delete(entry)

    def drop(self, request: ApprovalRequest) -> None:
        entry = self._entry(request)
        if entry is not None:
            self.store.drop(entry)

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        if self.draft is None:
            raise lifecycle().ApprovalSurfaceError("calendar approval payload unavailable")
        try:
            channel_id, message_id = calendar_confirm.post_confirmation_message(
                self.draft, intent.channel_id
            )
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        return lifecycle().PostedApproval(message_id=message_id, channel_id=channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        del created_at
        if self.draft is None:
            raise lifecycle().ApprovalRecordsError("calendar approval payload unavailable")
        binding = self._binding_for(intent)
        if posted.channel_id != binding.channel_id:
            raise lifecycle().ApprovalRecordsError("calendar approval channel binding changed")
        self.store.append(
            PendingConfirm(
                draft_id=str(self.draft["id"]),
                sha256=intent.action_hash,
                dm_channel_id=binding.channel_id,
                dm_message_id=posted.message_id,
                created=_draft_created(str(self.draft["created"])),
                key=intent.key,
                kind=str(binding.kind),
                surface=str(binding.surface),
                channel_id=binding.channel_id,
                policy_version=binding.policy_version,
            )
        )

    def _binding_for(self, intent: ApprovalIntent) -> calendar_binding.ApprovalBindingLike:
        binding = self.binding or calendar_binding.new_binding()
        if intent.channel_id != binding.channel_id:
            raise lifecycle().ApprovalSurfaceError("calendar approval intent binding changed")
        return binding

    def _entry(self, request: ApprovalRequest) -> PendingConfirm | None:
        try:
            matches = tuple(
                entry
                for entry in self.store.load()
                if request_of(entry) == request
            )
        except PendingConfirmError as error:
            raise lifecycle().ApprovalRecordsError(str(self.store.path)) from error
        return matches[0] if len(matches) == 1 else None


def request_confirmation(draft: DraftRecord) -> PendingConfirm:
    facade = lifecycle()
    store = PendingConfirmStore()
    binding = calendar_binding.new_binding()
    verdict = facade.request_owner_approval(
        confirm_intent(draft, binding),
        CalendarApprovalGate(draft, store, calendar_confirm.owner_id(), binding),
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
                (
                    request_of(entry)
                    for entry in store.load()
                    if entry.dm_message_id == posted.message_id
                ),
                None,
            )
        case outcome.PENDING:
            request = verdict.live
        case outcome.DEFERRED | outcome.REFUSED:
            reason = verdict.reason.value if verdict.reason is not None else "unknown"
            exit_code = 3 if reason in {"store-unreadable", "posting-journal-stale"} else 1
            raise calendar_gate.GateError(
                f"승인 메시지를 게시하지 않았습니다 ({verdict.outcome.value}:{reason})",
                exit_code,
            )
        case unreachable:
            assert_never(unreachable)
    if request is None:
        raise calendar_gate.GateError("승인 게시 결과가 비어 있습니다", 3)
    entry = CalendarApprovalGate(None, store, "")._entry(request)
    if entry is None:
        raise calendar_gate.GateError("pending confirm 결과를 찾을 수 없습니다", 3)
    return entry


def _draft_created(value: str) -> datetime:
    created = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise calendar_gate.GateError("드래프트 created UTC 누락", 3)
    return created.astimezone(UTC)
