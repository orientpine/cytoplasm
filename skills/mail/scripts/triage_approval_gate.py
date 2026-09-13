"""Mail lifecycle adapter, retaining facade injection seams."""
from __future__ import annotations
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.error import HTTPError
from importlib import import_module
import triage_confirm
import triage_binding
import triage_gate
if TYPE_CHECKING:
    from automation.interop.approval_lifecycle import ApprovalIntent, ApprovalRequest, PostedApproval, Probe

def refuse_unpostable_content(content: str) -> None:
    """Refuse exact post bytes BEFORE the journal reserves its key (t_82644d12).

    Existing bindings are resolved first without rendering; only a new post reaches
    this check, through the lifecycle's preparation callback under the key lease.
    """
    limit = import_module("triage_approval")._MESSAGE_LIMIT
    size = len(content)
    if size > limit:
        raise triage_gate.GateError(
            f"승인 메시지가 Discord 한도를 넘음({size}/{limit}자) — 게시하지 않음. "
            "본문을 줄여 다시 시도하거나 첨부 방식 승인이 필요하다.",
            3,
        )


@dataclass(frozen=True, slots=True)
class MailApprovalGate:
    """``approval_lifecycle.ApprovalGate`` over the triage draft store + Discord REST."""

    draft: dict
    notice: str = ""
    content: str = ""

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        approval = import_module("triage_approval")
        request_type = approval.lifecycle().ApprovalRequest
        return tuple(
            request_type(
                key=key,
                action_hash=approval._approval_action_hash(record),
                message_id=approval._bound_message_id(record),
                channel_id=approval._request_channel_id(record),
                created_at=str(record.get("approval_created_at", record["created"])),
            )
            for _, record, record_key in approval._pending_drafts()
            if record_key == key and approval._bound_message_id(record)
        )

    def probe(self, request: ApprovalRequest) -> Probe:
        approval = import_module("triage_approval")
        state = approval.lifecycle().Probe
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
        except approval._TRANSPORT_ERRORS as error:
            raise approval.lifecycle().ApprovalSurfaceError(str(error)) from error
        if triage_confirm._owner_reacted(cancel, owner):
            return state.CANCELLED
        if triage_confirm._owner_reacted(approve, owner):
            return state.APPROVED
        return state.BOUND_PENDING

    def _content(self, request: ApprovalRequest) -> str | None:
        approval = import_module("triage_approval")
        try:
            message = triage_confirm._api(
                "GET", f"/channels/{request.channel_id}/messages/{request.message_id}"
            )
        except HTTPError as error:
            if error.code == 404:
                return None
            raise approval.lifecycle().ApprovalSurfaceError(str(error)) from error
        except approval._TRANSPORT_ERRORS as error:
            raise approval.lifecycle().ApprovalSurfaceError(str(error)) from error
        if not isinstance(message, dict):
            raise approval.lifecycle().ApprovalSurfaceError("승인 메시지 응답이 유효하지 않음")
        return str(message.get("content", "")) or None

    def delete(self, request: ApprovalRequest) -> None:
        """Remove the superseded approval message before its record may be unbound."""
        approval = import_module("triage_approval")
        try:
            triage_confirm.delete_message(request.message_id, request.channel_id)
        except HTTPError as error:
            if error.code != 404:
                raise approval.lifecycle().ApprovalSurfaceError(str(error)) from error
        except approval._TRANSPORT_ERRORS as error:
            raise approval.lifecycle().ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        """Compare-and-swap: unbind the record ONLY while it still holds this message."""
        approval = import_module("triage_approval")
        bound = (request.action_hash, request.message_id)
        for path, record, key in approval._pending_drafts():
            if key != request.key or (approval._approval_action_hash(record), approval._bound_message_id(record)) != bound:
                continue
            unbound = {**record, "message_id": ""}
            if str(record["id"]) != str(self.draft["id"]):
                # 이 키의 최신 내용에 밀려난 형제 초안 — pending으로 두면 다음 tick이
                # 되받아 게시해 핑퐁이 된다.
                unbound["status"] = approval.SUPERSEDED_STATUS
            triage_gate.write_json(path, unbound)
            return

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        approval = import_module("triage_approval")
        content = self.content or approval._approval_content(self.draft, self.notice)
        message_id = triage_confirm.post_approval_request(content, intent.channel_id)
        for emoji in (triage_confirm.APPROVE_EMOJI, triage_confirm.CANCEL_EMOJI):
            try:
                triage_confirm.add_reaction(message_id, emoji, intent.channel_id)
            except HTTPError as error:
                reason = getattr(error, "code", None) or str(error)
                print(
                    f"APPROVAL-REACTION-FAIL {emoji} message={message_id} "
                    f"reason={type(error).__name__}:{reason}",
                    file=sys.stderr,
                )
        return approval.lifecycle().PostedApproval(message_id=message_id, channel_id=intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        """The ONLY writer of the Discord ``message_id`` and its timer anchor."""
        triage_gate.set_message_id(
            self.draft,
            posted.message_id,
            intent.channel_id,
            approval_created_at=created_at,
        )


def expire_retired_approval(draft: dict) -> bool:
    """Expire an undecided request whose persisted surface has been retired.

    The approval-key lease closes the reaction/delete/store race. An owner decision
    always wins: decided requests return to the normal resolver and are never expired.
    """
    approval = import_module("triage_approval")
    if not triage_binding.is_retired_binding(draft):
        return False
    created_at = draft.get("created")
    if not isinstance(created_at, str) or not created_at:
        return False
    key = approval.approval_key(draft)
    with approval.confirm_lease().hold(key) as owned:
        if not owned:
            raise triage_gate.GateError("승인 처리 lease 사용 중 — 다음 tick 재시도", 1)
        message_id = approval._bound_message_id(draft)
        if not message_id:
            triage_gate.expire_draft(str(draft["id"]), "", "approval-surface-retired")
            return True
        channel_id = triage_binding.persisted_channel_id(draft)
        if channel_id is None:
            channel_id = str(approval.stored_binding(draft).channel_id)
        request = approval.lifecycle().ApprovalRequest(
            key=key,
            action_hash=approval._approval_action_hash(draft),
            message_id=message_id,
            channel_id=channel_id,
            created_at=str(draft.get("approval_created_at", created_at)),
        )
        gate = MailApprovalGate(draft)
        probe = gate.probe(request)
        state = approval.lifecycle().Probe
        if probe in (state.APPROVED, state.CANCELLED):
            return False
        if probe is state.BOUND_PENDING:
            gate.delete(request)
        elif probe is not state.MISSING:
            raise triage_gate.GateError(
                f"폐지 승인 표면의 바인딩 검증 실패 ({probe.value}) — 만료 거부", 3,
            )
        triage_gate.expire_draft(str(draft["id"]), message_id, "approval-surface-retired")
        return True
