"""Budget request-mail adapter for the shared owner-approval lifecycle.

EXACTLY ONE live owner-approval request per ``budget:{mail_to}``. A budget draft
sends a real 과제비 request mail, so the stored ``message_id`` is written ONLY by
:meth:`BudgetApprovalGate.commit` — never replaced, only superseded (delete
BEFORE drop) or left alone. The surface is resolved ONCE per draft and persisted
with that message id, so every later action reads its channel from the record.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, assert_never
from urllib.error import HTTPError

import budget_binding
import budget_confirm
import budget_core
import budget_gate

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from automation.interop.approval_lease import ApprovalLease, PostingJournal
    from automation.interop.approval_lifecycle import (
        ApprovalIntent,
        ApprovalRequest,
        PostedApproval,
        Probe,
        Verdict,
    )

LEASE_DIRNAME = "approval-leases"
JOURNAL_DIRNAME = "posting-journal"
SUPERSEDED_STATUS = "superseded"
_TRANSPORT_ERRORS = (budget_gate.GateError, OSError, json.JSONDecodeError, KeyError, TypeError)


def lifecycle() -> ModuleType:
    """The shared approval façade — refuses the request when the repo is unreachable."""
    return budget_gate.repo_module("approval_lifecycle")


def _lease_module() -> ModuleType:
    return budget_gate.repo_module("approval_lease")


def approval_key(draft: dict) -> str:
    """The logical key one live request message belongs to."""
    recipient = draft.get("mail_to")
    if not isinstance(recipient, str) or not recipient:
        raise budget_gate.GateError("드래프트 mail_to 누락 — 승인 키를 만들 수 없음", 3)
    return f"budget:{recipient}"


def confirm_intent(draft: dict, binding: budget_gate.ApprovalBindingLike) -> ApprovalIntent:
    """The intent one approval post is bound to — key, draft digest, resolved surface."""
    digest = draft.get("sha256")
    if not isinstance(digest, str) or not digest:
        raise budget_gate.GateError("드래프트 sha256 누락 — 승인 게시 거부", 3)
    return lifecycle().ApprovalIntent(
        key=approval_key(draft),
        action_hash=digest,
        channel_id=binding.channel_id,
    )


def confirm_lease() -> ApprovalLease:
    return _lease_module().FileKeyLease(budget_gate.gate_dir() / LEASE_DIRNAME)


def posting_journal() -> PostingJournal:
    return _lease_module().PostingJournal(budget_gate.gate_dir() / JOURNAL_DIRNAME)


def _pending_drafts() -> tuple[tuple[Path, dict], ...]:
    """(path, record) for every pending draft — ANY unreadable record fails closed."""
    records: list[tuple[Path, dict]] = []
    for path in sorted((budget_gate.gate_dir() / "drafts").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise lifecycle().ApprovalRecordsError(str(path)) from error
        if not isinstance(data, dict):
            raise lifecycle().ApprovalRecordsError(str(path))
        if data.get("status") == "pending":
            records.append((path, data))
    return tuple(records)


def _bound_message_id(record: dict) -> str:
    message_id = record.get("message_id")
    return message_id if isinstance(message_id, str) else ""


@dataclass(frozen=True, slots=True)
class BudgetApprovalGate:
    """``approval_lifecycle.ApprovalGate`` over the budget draft store + Discord REST."""

    draft: dict
    binding: budget_gate.ApprovalBindingLike

    @property
    def channel_id(self) -> str:
        return self.binding.channel_id

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        request_type = lifecycle().ApprovalRequest
        return tuple(
            request_type(
                key=key,
                action_hash=str(record["sha256"]),
                message_id=_bound_message_id(record),
                channel_id=budget_binding.stored_binding(record).channel_id,
                created_at=str(record["created"]),
            )
            for _, record in _pending_drafts()
            if approval_key(record) == key and _bound_message_id(record)
        )

    def probe(self, request: ApprovalRequest) -> Probe:
        state = lifecycle().Probe
        content = self._content(request)
        if content is None:
            return state.MISSING
        if request.action_hash not in content:
            return state.BINDING_MISMATCH
        channel, message = request.channel_id, request.message_id
        try:
            owner = budget_confirm.owner_id()
            cancel = budget_confirm._reaction_users(channel, message, budget_confirm.CANCEL_EMOJI)
            approve = budget_confirm._reaction_users(channel, message, budget_confirm.APPROVE_EMOJI)
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        if budget_confirm._owner_reacted(cancel, owner):
            return state.CANCELLED
        if budget_confirm._owner_reacted(approve, owner):
            return state.APPROVED
        return state.BOUND_PENDING

    def _content(self, request: ApprovalRequest) -> str | None:
        try:
            message = budget_confirm._api(
                "GET", f"/channels/{request.channel_id}/messages/{request.message_id}"
            )
        except HTTPError as error:
            if error.code == 404:
                return None
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        if not isinstance(message, dict):
            raise lifecycle().ApprovalSurfaceError("승인 메시지 응답이 유효하지 않음")
        return str(message.get("content", "")) or None

    def delete(self, request: ApprovalRequest) -> None:
        """Remove the superseded message from its OWN channel before the record is unbound."""
        try:
            budget_confirm._api(
                "DELETE", f"/channels/{request.channel_id}/messages/{request.message_id}"
            )
        except HTTPError as error:
            if error.code != 404:
                raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        """Compare-and-swap: unbind the record ONLY while it still holds this message."""
        bound = (request.action_hash, request.message_id)
        for path, record in _pending_drafts():
            if (str(record["sha256"]), _bound_message_id(record)) != bound:
                continue
            unbound = {**record, "message_id": ""}
            if str(record["id"]) != str(self.draft["id"]):
                # 최신 원장 변경에 밀려난 형제 초안 — pending으로 두면 다음 30분 tick이
                # 되받아 게시해 핑퐁이 된다.
                unbound["status"] = SUPERSEDED_STATUS
            budget_gate.write_json(path, unbound)
            return

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        content = budget_core.render_approvals_message(
            self.draft,
            instruction=budget_binding.reaction_instruction(self.draft),
        )
        channel_id = intent.channel_id
        message_id = budget_confirm.post_approval_request(content, channel_id)
        budget_confirm.add_reaction(message_id, budget_confirm.APPROVE_EMOJI, channel_id)
        budget_confirm.add_reaction(message_id, budget_confirm.CANCEL_EMOJI, channel_id)
        return lifecycle().PostedApproval(message_id=message_id, channel_id=intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        """The ONLY writer of the Discord ``message_id`` and its channel binding."""
        del intent, created_at
        budget_gate.set_message_id(self.draft, posted.message_id, self.binding)


def request_approval(draft: dict) -> Verdict:
    """Run the shared lifecycle for one draft while holding its key's lease."""
    binding = budget_binding.binding_for(draft)
    intent = confirm_intent(draft, binding)
    return lifecycle().request_owner_approval(
        intent,
        BudgetApprovalGate(draft, binding),
        confirm_lease(),
        posting_journal(),
    )


def _refusal(verdict: Verdict) -> budget_gate.GateError:
    reason = verdict.reason.value if verdict.reason is not None else "unknown"
    exit_code = 3 if reason in {"store-unreadable", "posting-journal-stale"} else 1
    return budget_gate.GateError(
        f"승인 메시지를 게시하지 않았습니다 ({verdict.outcome.value}:{reason})"
        " — 기존 승인 메시지가 유효합니다", exit_code,
    )


def _owns(request: ApprovalRequest, draft: dict) -> bool:
    """True iff the live request is bound to THIS draft record, never a sibling."""
    binding = (request.action_hash, request.message_id)
    try:
        records = _pending_drafts()
    except lifecycle().ApprovalRecordsError:
        return False
    return any(
        str(record["id"]) == str(draft["id"])
        and (str(record["sha256"]), _bound_message_id(record)) == binding
        for _, record in records
    )


def bound_message_id(verdict: Verdict, draft: dict) -> str:
    """Map one lifecycle verdict onto the legacy ``_post_draft_for_approval`` contract."""
    outcome = lifecycle().Outcome
    match verdict.outcome:
        case outcome.POSTED:
            posted = verdict.posted
            if posted is None:
                raise budget_gate.GateError("승인 게시 결과가 비어 있음 — 거부", 3)
            return posted.message_id
        case outcome.PENDING:
            live = verdict.live
            if live is None or not _owns(live, draft):
                raise budget_gate.GateError(
                    "다른 초안이 이 키의 승인 메시지를 보유 중 — 게시 거부", 1
                )
            return live.message_id
        case outcome.DEFERRED | outcome.REFUSED:
            raise _refusal(verdict)
        case unreachable:
            assert_never(unreachable)


def post_for_approval(draft: dict) -> str:
    """Producer entry point — one guarded post/reaction/bind sequence per key."""
    return bound_message_id(request_approval(draft), draft)
