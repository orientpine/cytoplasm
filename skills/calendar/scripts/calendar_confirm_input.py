"""Calendar owner-input validation; facade transport seams remain injectable."""
from __future__ import annotations
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, NotRequired, TypedDict
from importlib import import_module
from calendar_pending import PendingConfirm


class DraftRecord(TypedDict):
    id: str
    sha256: str
    created: str
    action: str
    channel_id: str
    calendar_id: str
    event_id: str
    summary: str
    start: str
    end: str
    argv: NotRequired[list[str]]
    status: NotRequired[str]
    origin_channel_id: NotRequired[str]
    origin_message_id: NotRequired[str]
    approval_thread_id: NotRequired[str]
    approval_guild_id: NotRequired[str]
    render_version: NotRequired[str]
    approval_content: NotRequired[str]
    digest_day: NotRequired[str]
    digest_key: NotRequired[str]
    dm_message_id: NotRequired[str]


def _adapter() -> Any:
    confirm = import_module("calendar_confirm")
    runtime = Path(os.environ.get("INTEROP_RUNTIME", "~/.hermes/interop_runtime")).expanduser()
    sys.path.insert(0, str(runtime))
    try:
        from automation.interop import injection_adapter
    except ImportError:
        raise confirm.GateError(f"injection adapter 불가 (INTEROP_RUNTIME={runtime})", 3) from None
    return injection_adapter

def _require_e2e_secret() -> str:
    confirm = import_module("calendar_confirm")
    if os.environ.get("E2E_TEST_MODE") != "1":
        raise confirm.GateError("주입 경로는 E2E_TEST_MODE=1 전용입니다 (프로덕션 게이트웨이 금지)", 1)
    secret = os.environ.get("INTEROP_E2E_SECRET", "")
    if not secret:
        raise confirm.GateError("INTEROP_E2E_SECRET 누락", 1)
    return secret

def confirm_via_injection(draft: DraftRecord, injection_path: Path) -> str:
    confirm = import_module("calendar_confirm")
    secret = confirm._require_e2e_secret()
    adapter = confirm._adapter()
    envelope = json.loads(injection_path.read_text(encoding="utf-8"))
    event = adapter.InboundEvent(
        event_id=str(envelope["event"]["event_id"]),
        user_id=str(envelope["event"]["user_id"]),
        channel_id=str(envelope["event"]["channel_id"]),
        text=str(envelope["event"]["text"]),
    )
    if not adapter.accept_test_event(
        event, str(envelope["signature"]), secret.encode("utf-8"), e2e_test_mode=True
    ):
        raise confirm.GateError("주입 승인 서명 불일치 — 거부", 1)
    if event.user_id != confirm.owner_id():
        raise confirm.GateError("주입 승인 발신자가 소유자가 아님 — 거부", 1)
    if event.channel_id != draft["channel_id"]:
        raise confirm.GateError("주입 승인 채널 불일치 — 거부", 1)
    if event.text != confirm.confirm_text(draft):
        raise confirm.GateError("주입 승인 텍스트/해시 불일치 — 거부", 1)
    return f"injected:{event.event_id}"

def sign_injection(
    draft: DraftRecord, out_path: Path, user_id: str | None, channel_id: str | None, forge_signature: bool
) -> None:
    confirm = import_module("calendar_confirm")
    secret = confirm._require_e2e_secret()
    adapter = confirm._adapter()
    event = adapter.InboundEvent(
        event_id=str(uuid.uuid4()),
        user_id=user_id or confirm.owner_id(),
        channel_id=channel_id or draft["channel_id"],
        text=confirm.confirm_text(draft),
    )
    signature = "0" * 64 if forge_signature else adapter.sign_event(event, secret.encode("utf-8"))
    confirm.write_json(
        out_path,
        {
            "event": {
                "event_id": event.event_id,
                "user_id": event.user_id,
                "channel_id": event.channel_id,
                "text": event.text,
            },
            "signature": signature,
        },
    )

def confirm_via_owner_scan(draft: DraftRecord) -> str:
    confirm = import_module("calendar_confirm")
    owner = confirm.owner_id()
    channel_id = confirm.calendar_binding.approval_directory().owner_dm()
    messages = confirm._api("GET", f"/channels/{channel_id}/messages?limit={confirm.DM_SCAN_LIMIT}")
    accepted = {f"실행 {draft['id']}", confirm.confirm_text(draft)}
    for message in messages:
        author = message.get("author", {})
        if str(author.get("id", "")) != owner or bool(author.get("bot", False)):
            continue
        if str(message.get("content", "")).strip() not in accepted:
            continue
        if confirm._parse_ts(str(message["timestamp"])) < confirm._parse_ts(str(draft["created"])):
            continue
        return f"dm:{message['id']}"
    raise confirm.GateError(
        f"소유자의 '실행 {draft['id']}' DM 확인을 찾지 못함 — 실행하지 않습니다", 1
    )

def _pending_entry(draft_id: str, *, required: bool = True) -> PendingConfirm | None:
    confirm = import_module("calendar_confirm")
    try:
        entries = [entry for entry in confirm.PendingConfirmStore().load() if entry.draft_id == draft_id]
    except confirm.PendingConfirmError as error:
        raise confirm.GateError("pending confirm store를 신뢰할 수 없습니다", 3) from error
    if len(entries) == 1:
        return entries[0]
    if not required and not entries:
        return None
    raise confirm.GateError("반응 확인용 pending confirm이 유일하지 않습니다", 1)

def _validate_pending_binding(draft: DraftRecord, entry: PendingConfirm) -> None:
    confirm = import_module("calendar_confirm")
    if draft.get("sha256") != entry.sha256:
        raise confirm.GateError("pending confirm 드래프트 해시 불일치", 1)
    if f"sha256:{entry.sha256}" not in confirm.confirmation_message_content(entry):
        raise confirm.GateError("확정 DM 드래프트 해시 불일치", 1)

def _reaction_action(entry: PendingConfirm, owner: str) -> str:
    confirm = import_module("calendar_confirm")
    cancel = confirm._owner_reacted(confirm.confirmation_reaction_users(entry, confirm.CANCEL_EMOJI), owner)
    approve = confirm._owner_reacted(confirm.confirmation_reaction_users(entry, confirm.APPROVE_EMOJI), owner)
    if cancel:
        return confirm.CANCEL_EMOJI
    if approve:
        return confirm.APPROVE_EMOJI
    return ""

def _owner_reacted(users: tuple[dict[str, str | bool], ...], owner: str) -> bool:
    return any(user.get("id", "") == owner and not bool(user.get("bot", False)) for user in users)
