"""Mail-triage adapter for the shared owner-approval lifecycle.

EXACTLY ONE live approval message per ``mail:{kind}:{uid}``. The draft field
bound here is ``message_id`` — the DISCORD approval message id, written ONLY by
:meth:`MailApprovalGate.commit`. It is NOT the RFC 5322 ``Message-ID`` of the
mail being answered: that header never reaches a draft record (the answered mail
is identified by ``uid`` / ``uid_opaque``), so the two cannot be confused.

A stored id is never replaced — only superseded (delete BEFORE drop) or left
alone. The repo façade is imported lazily through ``AUTOPHAGY_REPO_ROOT``; an
``ImportError`` refuses the request instead of falling back to an unguarded post.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, assert_never
from urllib.error import HTTPError

import triage_confirm
import triage_binding
import triage_core
import triage_gate

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
_TRANSPORT_ERRORS = (triage_gate.GateError, OSError, json.JSONDecodeError, KeyError, TypeError)


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
        raise triage_gate.GateError(
            f"승인 라이프사이클 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — 승인 게시 거부", 3
        ) from None


def lifecycle() -> ModuleType:
    """The shared approval façade — refuses the request when the repo is unreachable."""
    return _repo_module("approval_lifecycle")


def _lease_module() -> ModuleType:
    return _repo_module("approval_lease")


def approval_key(draft: dict) -> str:
    """The logical key one live approval message belongs to."""
    kind, uid = draft.get("kind"), draft.get("uid")
    if kind is None:
        kind = "reply"
    if not isinstance(kind, str) or kind not in {"reply", "compose"} or not isinstance(uid, str) or not uid:
        raise triage_gate.GateError("드래프트 kind/uid 누락 — 승인 키를 만들 수 없음", 3)
    return f"mail:{kind}:{uid}"


approval_directory = triage_binding.approval_directory
stored_binding = triage_binding.stored_binding
reaction_instruction = triage_binding.reaction_instruction


def post_channel_id(draft: dict) -> str:
    return str(stored_binding(draft).channel_id)


def confirm_intent(draft: dict) -> ApprovalIntent:
    """The intent one approval post is bound to — key, draft digest, channel."""
    digest = draft.get("sha256")
    if not isinstance(digest, str) or not digest:
        raise triage_gate.GateError("드래프트 sha256 누락 — 승인 게시 거부", 3)
    binding = stored_binding(draft)
    triage_gate.set_approval_binding(
        draft,
        kind=triage_binding.draft_kind(draft),
        surface=str(binding.surface),
        channel_id=str(binding.channel_id),
        policy_version=int(binding.policy_version),
    )
    return lifecycle().ApprovalIntent(
        key=approval_key(draft), action_hash=_approval_action_hash(draft), channel_id=str(binding.channel_id)
    )


def confirm_lease() -> ApprovalLease:
    return _lease_module().FileKeyLease(triage_gate.gate_dir() / LEASE_DIRNAME)


def posting_journal() -> PostingJournal:
    return _lease_module().PostingJournal(triage_gate.gate_dir() / JOURNAL_DIRNAME)


def _pending_drafts() -> tuple[tuple[Path, dict, str], ...]:
    """(path, record, key) for every pending draft — ANY unreadable record fails closed."""
    records: list[tuple[Path, dict, str]] = []
    directories = (triage_gate._public_drafts_dir(), triage_gate._sensitive_drafts_dir())
    for path in sorted(item for directory in directories for item in directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise lifecycle().ApprovalRecordsError(str(path)) from error
        if not isinstance(data, dict):
            raise lifecycle().ApprovalRecordsError(str(path))
        if data.get("status") != "pending":
            continue
        try:
            records.append((path, data, approval_key(data)))
        except triage_gate.GateError as error:
            raise lifecycle().ApprovalRecordsError(str(path)) from error
    return tuple(records)


def _bound_message_id(record: dict) -> str:
    message_id = record.get("message_id")
    return message_id if isinstance(message_id, str) else ""


def _approval_action_hash(record: dict) -> str:
    """Use Gmail's external-effect hash while preserving legacy mail draft binding."""
    action_hash = record.get("approval_action_hash")
    if record.get("provider") == "gmail":
        if not isinstance(action_hash, str) or not action_hash.startswith("sha256:"):
            raise triage_gate.GateError("Gmail action hash 누락 — 승인 게시 거부", 3)
        return action_hash
    return str(record["sha256"])


def _request_channel_id(record: dict) -> str:
    return str(stored_binding(record).channel_id)


@dataclass(frozen=True, slots=True)
class MailApprovalGate:
    """``approval_lifecycle.ApprovalGate`` over the triage draft store + Discord REST."""

    draft: dict
    notice: str = ""

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        request_type = lifecycle().ApprovalRequest
        return tuple(
            request_type(
                key=key,
                action_hash=_approval_action_hash(record),
                message_id=_bound_message_id(record),
                channel_id=_request_channel_id(record),
                created_at=str(record["created"]),
            )
            for _, record, record_key in _pending_drafts()
            if record_key == key and _bound_message_id(record)
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
            owner = triage_confirm.owner_id()
            cancel = triage_confirm._reaction_users(channel, message, triage_confirm.CANCEL_EMOJI)
            approve = triage_confirm._reaction_users(channel, message, triage_confirm.APPROVE_EMOJI)
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        if triage_confirm._owner_reacted(cancel, owner):
            return state.CANCELLED
        if triage_confirm._owner_reacted(approve, owner):
            return state.APPROVED
        return state.BOUND_PENDING

    def _content(self, request: ApprovalRequest) -> str | None:
        try:
            message = triage_confirm._api(
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
        """Remove the superseded approval message before its record may be unbound."""
        try:
            triage_confirm.delete_message(request.message_id, request.channel_id)
        except HTTPError as error:
            if error.code != 404:
                raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        """Compare-and-swap: unbind the record ONLY while it still holds this message."""
        bound = (request.action_hash, request.message_id)
        for path, record, key in _pending_drafts():
            if key != request.key or (_approval_action_hash(record), _bound_message_id(record)) != bound:
                continue
            unbound = {**record, "message_id": ""}
            if str(record["id"]) != str(self.draft["id"]):
                # 이 키의 최신 내용에 밀려난 형제 초안 — pending으로 두면 다음 tick이
                # 되받아 게시해 핑퐁이 된다.
                unbound["status"] = SUPERSEDED_STATUS
            triage_gate.write_json(path, unbound)
            return

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        content = (
            triage_core.render_approvals_message(
                self.draft,
                destination=triage_core.ApprovalRenderDestination.OWNER_DM,
                instruction=reaction_instruction(self.draft),
            )
            + self.notice
        )
        message_id = triage_confirm.post_approval_request(content, intent.channel_id)
        triage_confirm.add_reaction(message_id, triage_confirm.APPROVE_EMOJI, intent.channel_id)
        triage_confirm.add_reaction(message_id, triage_confirm.CANCEL_EMOJI, intent.channel_id)
        return lifecycle().PostedApproval(message_id=message_id, channel_id=intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        """The ONLY writer of the Discord ``message_id`` in the mail skill."""
        del created_at
        triage_gate.set_message_id(self.draft, posted.message_id, intent.channel_id)


def request_approval(draft: dict, *, notice: str = "") -> Verdict:
    """Run the shared lifecycle for one draft while holding its key's lease."""
    return lifecycle().request_owner_approval(
        confirm_intent(draft), MailApprovalGate(draft, notice), confirm_lease(), posting_journal()
    )


def _refusal(verdict: Verdict) -> triage_gate.GateError:
    reason = verdict.reason.value if verdict.reason is not None else "unknown"
    exit_code = 3 if reason in {"store-unreadable", "posting-journal-stale"} else 1
    return triage_gate.GateError(
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
        and (_approval_action_hash(record), _bound_message_id(record)) == binding
        for _, record, _ in records
    )


def bound_message_id(verdict: Verdict, draft: dict) -> str:
    """Map one lifecycle verdict onto the legacy ``_post_draft_for_approval`` contract."""
    outcome = lifecycle().Outcome
    match verdict.outcome:
        case outcome.POSTED:
            posted = verdict.posted
            if posted is None:
                raise triage_gate.GateError("승인 게시 결과가 비어 있음 — 거부", 3)
            return posted.message_id
        case outcome.PENDING:
            live = verdict.live
            if live is None or not _owns(live, draft):
                raise triage_gate.GateError(
                    "다른 초안이 이 키의 승인 메시지를 보유 중 — 게시 거부", 1
                )
            return live.message_id
        case outcome.DEFERRED | outcome.REFUSED:
            raise _refusal(verdict)
        case unreachable:
            assert_never(unreachable)


def post_for_approval(draft: dict, *, notice: str = "") -> str:
    """Producer entry point — one guarded post/reaction/bind sequence per key."""
    return bound_message_id(request_approval(draft, notice=notice), draft)
