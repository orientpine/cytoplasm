"""Shared-lifecycle adapter for one Google Tasks approval request."""
from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import ModuleType
from typing import TYPE_CHECKING, Final
from urllib.error import HTTPError

from todo_approval_ports import DirectoryLike, TransportLike
from todo_approval_store import (
    ApprovalState,
    TodoApprovalRecord,
    TodoApprovalSpec,
    TodoApprovalStore,
    TodoApprovalStoreError,
)

if TYPE_CHECKING:
    from automation.interop.approval_lease import ApprovalLease, PostingJournal
    from automation.interop.approval_lifecycle import (
        ApprovalIntent,
        ApprovalRequest,
        PostedApproval,
        Probe,
        Verdict,
    )
    from automation.interop.approval_surface import ApprovalBinding


APPROVE_EMOJI: Final = "✅"
CANCEL_EMOJI: Final = "⛔"
_TRANSPORT_ERRORS: Final = (OSError, RuntimeError, ValueError, KeyError, TypeError)


class TodoApprovalError(RuntimeError):
    """Approval production failed closed before a valid lifecycle result."""


@dataclass(frozen=True, slots=True)
class TodoApprovalIntent:
    action_hash: str
    target_id: str
    argv_summary: str
    title: str
    due: str | None
    origin_channel_id: str = ""
    origin_message_id: str = ""
    tasklist: str = ""
    notes: str | None = None

    @property
    def key(self) -> str:
        return f"todo:{self.action_hash}"


@dataclass(frozen=True, slots=True)
class ApprovalRuntime:
    store: TodoApprovalStore
    transport: TransportLike
    directory: DirectoryLike
    owner_id: str
    binding: ApprovalBinding
    lease: ApprovalLease
    journal: PostingJournal
    now: Callable[[], datetime]


def masked_argv_summary(argv: Sequence[str]) -> str:
    masked: list[str] = []
    hide_next = False
    for value in argv:
        if hide_next:
            masked.append("[masked]")
            hide_next = False
        else:
            masked.append(value)
            hide_next = value in {"--params", "--json"}
    return " ".join(masked)


def _repo_module(name: str) -> ModuleType:
    preflight = importlib.import_module("todo_preflight")
    root = preflight.repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        return importlib.import_module(f"automation.interop.{name}")
    except ImportError as error:
        raise TodoApprovalError(f"shared approval module unavailable: {name}") from error


def lifecycle() -> ModuleType:
    return _repo_module("approval_lifecycle")


def surface_module() -> ModuleType:
    return _repo_module("approval_surface")


@dataclass(frozen=True, slots=True)
class TodoApprovalGate:
    intent: TodoApprovalIntent
    runtime: ApprovalRuntime

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        if key != self.intent.key:
            return ()
        try:
            _ = self.runtime.store.prepare(self._spec(), self.runtime.now())
            request_type = lifecycle().ApprovalRequest
            return tuple(request_type(
                record.key,
                record.action_hash,
                record.message_id,
                record.channel_id,
                record.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ) for record in self.runtime.store.outstanding(key) if record.message_id is not None)
        except TodoApprovalStoreError as error:
            raise lifecycle().ApprovalRecordsError(str(error)) from error

    def probe(self, request: ApprovalRequest) -> Probe:
        shared = lifecycle()
        record = self._record(request)
        if record is None:
            return shared.Probe.BINDING_MISMATCH
        self._validate_binding(record)
        try:
            content = self.runtime.transport.get_message(request.channel_id, request.message_id)
            if content is None:
                return shared.Probe.MISSING
            if request.action_hash not in content:
                return shared.Probe.BINDING_MISMATCH
            cancelled = self.runtime.transport.get_reaction_users(
                request.channel_id, request.message_id, CANCEL_EMOJI
            )
            approved = self.runtime.transport.get_reaction_users(
                request.channel_id, request.message_id, APPROVE_EMOJI
            )
        except HTTPError as error:
            if error.code == 404:
                return shared.Probe.MISSING
            raise shared.ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise shared.ApprovalSurfaceError(str(error)) from error
        if _owner_reacted(cancelled, self.runtime.owner_id):
            return shared.Probe.CANCELLED
        if _owner_reacted(approved, self.runtime.owner_id):
            return shared.Probe.APPROVED
        return shared.Probe.BOUND_PENDING

    def delete(self, request: ApprovalRequest) -> None:
        try:
            self.runtime.transport.delete_message(request.channel_id, request.message_id)
        except HTTPError as error:
            if error.code != 404:
                raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        record = self._record(request)
        if record is None:
            raise lifecycle().ApprovalRecordsError("todo pending binding changed")
        try:
            self.runtime.store.archive(record, ApprovalState.ARCHIVED, None)
        except TodoApprovalStoreError as error:
            raise lifecycle().ApprovalRecordsError(str(error)) from error

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        if intent.key != self.intent.key or intent.action_hash != self.intent.action_hash:
            raise lifecycle().ApprovalSurfaceError("todo approval intent changed")
        try:
            message_id = self.runtime.transport.post_message(intent.channel_id, self._render())
            self.runtime.transport.add_reaction(intent.channel_id, message_id, APPROVE_EMOJI)
            self.runtime.transport.add_reaction(intent.channel_id, message_id, CANCEL_EMOJI)
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        return lifecycle().PostedApproval(message_id, intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        del created_at
        if posted.channel_id != self.runtime.binding.channel_id or intent.key != self.intent.key:
            raise lifecycle().ApprovalRecordsError("todo approval channel binding changed")
        record = self.runtime.store.active(self.intent.key)
        if record is None:
            raise lifecycle().ApprovalRecordsError("todo pending generation is absent")
        try:
            self.runtime.store.bind_message(record, posted.message_id)
        except TodoApprovalStoreError as error:
            raise lifecycle().ApprovalRecordsError(str(error)) from error

    def _spec(self) -> TodoApprovalSpec:
        binding = self.runtime.binding
        return TodoApprovalSpec(
            self.intent.key,
            self.intent.action_hash,
            self.intent.target_id,
            self.intent.argv_summary,
            str(binding.kind),
            str(binding.surface),
            binding.channel_id,
            binding.policy_version,
            origin_channel_id=self.intent.origin_channel_id,
            origin_message_id=self.intent.origin_message_id,
            tasklist=self.intent.tasklist,
            title=self.intent.title,
            notes=self.intent.notes,
            due=self.intent.due,
        )

    def _record(self, request: ApprovalRequest) -> TodoApprovalRecord | None:
        matches = tuple(
            record
            for record in self.runtime.store.outstanding(request.key)
            if record.message_id == request.message_id
            and record.action_hash == request.action_hash
            and record.channel_id == request.channel_id
        )
        return matches[0] if len(matches) == 1 else None

    def _validate_binding(self, record: TodoApprovalRecord) -> None:
        surface = surface_module()
        binding = surface.ApprovalBinding(
            surface.ApprovalKind(record.kind),
            surface.ApprovalSurface(record.surface),
            record.channel_id,
            record.policy_version,
        )
        try:
            surface.validate_stored_binding(binding, self.runtime.directory, self.runtime.owner_id)
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error

    def _render(self) -> str:
        due = self.intent.due or "-"
        instruction = surface_module().reaction_instruction(
            self.runtime.binding.kind,
            self.runtime.binding.surface,
        )
        return (
            "Google Tasks 등록 승인\n"
            f"제목: {self.intent.title}\n"
            f"기한: {due}\n"
            f"argv hash: {self.intent.action_hash}\n"
            f"{instruction}"
        )


def request_approval(intent: TodoApprovalIntent, runtime: ApprovalRuntime) -> Verdict:
    shared = lifecycle()
    approval_intent = shared.ApprovalIntent(
        intent.key,
        intent.action_hash,
        runtime.binding.channel_id,
    )
    return shared.request_owner_approval(
        approval_intent,
        TodoApprovalGate(intent, runtime),
        runtime.lease,
        runtime.journal,
    )


def request_cli_approval(intent: TodoApprovalIntent, owner: str) -> Verdict:
    runtime_module = importlib.import_module("todo_approval_runtime")
    return runtime_module.request_cli_approval(intent, owner)


def _owner_reacted(users: tuple[tuple[str, bool], ...], owner_id: str) -> bool:
    return any(user_id == owner_id and not is_bot for user_id, is_bot in users)
