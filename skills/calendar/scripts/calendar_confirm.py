"""Owner-confirmation transports for the calendar gate (W3-1).

Three mutually exclusive paths, all fail-closed:
- production direct ``owner_dm_reply``/reaction: owner input verified through Discord REST;
- watcher ``owner_dm_reaction``: a short-lived HMAC authorization, bound to the exact
  draft/pending DM and consumed once without a duplicate Discord query;
- unattended ``signed_injection_e2e``: HMAC-signed injected event (W1-6 adapter)
  accepted only under ``E2E_TEST_MODE=1`` (production gateway refuses that env).
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from contextlib import suppress
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Any, TypeAlias
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

calendar_core = import_module("calendar_core")
_gate = import_module("calendar_gate")
GateError = _gate.GateError
write_json = _gate.write_json
_pending = import_module("calendar_pending")
PendingConfirm = _pending.PendingConfirm
PendingConfirmError = _pending.PendingConfirmError
PendingConfirmStore = _pending.PendingConfirmStore
calendar_binding = import_module("calendar_binding")
_authz = import_module("calendar_confirm_authz")
create_watcher_authorization = _authz.create_watcher_authorization

DraftRecord: TypeAlias = dict[str, str | list[str]]

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
DM_SCAN_LIMIT = 50
APPROVE_EMOJI = "\u2705"
CANCEL_EMOJI = "\u26d4"


def confirm_text(draft: DraftRecord) -> str:
    return f"실행 {draft['id']} sha256:{draft['sha256']}"


def owner_id() -> str:
    config = Path(os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")).expanduser()
    try:
        owner = json.loads(config.read_text(encoding="utf-8")).get("owner_id")
    except OSError:
        raise GateError(f"interop config 읽기 실패: {config}", 3) from None
    if not isinstance(owner, str) or not owner:
        raise GateError("interop config에 owner_id가 없습니다", 3)
    return owner


def _adapter() -> Any:
    runtime = Path(os.environ.get("INTEROP_RUNTIME", "~/.hermes/interop_runtime")).expanduser()
    sys.path.insert(0, str(runtime))
    try:
        from automation.interop import injection_adapter
    except ImportError:
        raise GateError(f"injection adapter 불가 (INTEROP_RUNTIME={runtime})", 3) from None
    return injection_adapter


def _require_e2e_secret() -> str:
    if os.environ.get("E2E_TEST_MODE") != "1":
        raise GateError("주입 경로는 E2E_TEST_MODE=1 전용입니다 (프로덕션 게이트웨이 금지)", 1)
    secret = os.environ.get("INTEROP_E2E_SECRET", "")
    if not secret:
        raise GateError("INTEROP_E2E_SECRET 누락", 1)
    return secret


def bot_token() -> str:
    if token := os.environ.get("DISCORD_BOT_TOKEN", "").strip():
        return token
    with suppress(FileNotFoundError):
        for line in (Path.home() / ".env.secrets").read_text(encoding="utf-8").splitlines():
            key, delimiter, value = line.strip().partition("=")
            if key.strip() == "DISCORD_BOT_TOKEN" and delimiter and (token := value.strip().strip("'\"").strip()):
                return token
    raise GateError("DISCORD_BOT_TOKEN 누락 — 프로덕션 확인 경로 사용 불가", 3)


def consume_watcher_authorization(draft: DraftRecord, authorization_path: Path) -> str:
    """Atomically consume one watcher authorization bound to this exact draft/DM.

    서명·잠금·nonce 소진은 `calendar_confirm_authz` 가 소유한다. 소유자 판정과 pending
    조회는 이 모듈이 소유하므로 호출 시점에 그대로 넘긴다 — 사본을 만들지 않는다.
    """
    return _authz.consume_watcher_authorization(
        draft,
        authorization_path,
        _authz.WatcherAuthorizationBindings(owner_id=owner_id, pending_entry=_pending_entry),
    )


def confirm_via_injection(draft: DraftRecord, injection_path: Path) -> str:
    secret = _require_e2e_secret()
    adapter = _adapter()
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
        raise GateError("주입 승인 서명 불일치 — 거부", 1)
    if event.user_id != owner_id():
        raise GateError("주입 승인 발신자가 소유자가 아님 — 거부", 1)
    if event.channel_id != draft["channel_id"]:
        raise GateError("주입 승인 채널 불일치 — 거부", 1)
    if event.text != confirm_text(draft):
        raise GateError("주입 승인 텍스트/해시 불일치 — 거부", 1)
    return f"injected:{event.event_id}"


def sign_injection(
    draft: DraftRecord, out_path: Path, user_id: str | None, channel_id: str | None, forge_signature: bool
) -> None:
    secret = _require_e2e_secret()
    adapter = _adapter()
    event = adapter.InboundEvent(
        event_id=str(uuid.uuid4()),
        user_id=user_id or owner_id(),
        channel_id=channel_id or draft["channel_id"],
        text=confirm_text(draft),
    )
    signature = "0" * 64 if forge_signature else adapter.sign_event(event, secret.encode("utf-8"))
    write_json(
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


def _api(method: str, path: str, payload: dict[str, str] | None = None) -> Any:
    token = bot_token()
    request = Request(
        f"{API}{path}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method=method,
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def confirm_via_owner_scan(draft: DraftRecord) -> str:
    owner = owner_id()
    channel_id = calendar_binding.approval_directory().owner_dm()
    messages = _api("GET", f"/channels/{channel_id}/messages?limit={DM_SCAN_LIMIT}")
    accepted = {f"실행 {draft['id']}", confirm_text(draft)}
    for message in messages:
        author = message.get("author", {})
        if str(author.get("id", "")) != owner or bool(author.get("bot", False)):
            continue
        if str(message.get("content", "")).strip() not in accepted:
            continue
        if _parse_ts(str(message["timestamp"])) < _parse_ts(str(draft["created"])):
            continue
        return f"dm:{message['id']}"
    raise GateError(
        f"소유자의 '실행 {draft['id']}' DM 확인을 찾지 못함 — 실행하지 않습니다", 1
    )


def post_confirmation_message(draft: DraftRecord, channel_id: str) -> tuple[str, str]:
    """Post the owner confirmation message and pre-add the two reaction choices."""
    content = (
        f"{_change_summary(draft)}\n\n{calendar_binding.reaction_instruction()}. "
        f"텍스트 fallback: `실행 {draft['id']}`/`취소 {draft['id']}`\n"
        f"sha256:{draft['sha256']}"
    )
    message = _api("POST", f"/channels/{channel_id}/messages", {"content": content})
    message_id = _required_string(message, "id", "확정 DM 메시지 id가 없습니다")
    _api("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(APPROVE_EMOJI, safe='')}/@me")
    _api("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(CANCEL_EMOJI, safe='')}/@me")
    return channel_id, message_id


def owner_approval_channel(owner: str) -> str:
    """Resolve the configured owner's DM through the shared approval directory."""
    if owner != owner_id():
        raise GateError("승인 소유자 id가 현재 interop 설정과 다릅니다", 3)
    return calendar_binding.approval_directory().owner_dm()


def send_owner_dm(owner: str, content: str) -> None:
    """Send a private, terse result notification to the calendar owner."""
    if owner != owner_id():
        raise GateError("승인 소유자 id가 현재 interop 설정과 다릅니다", 3)
    channel_id = calendar_binding.approval_directory().owner_dm()
    _api("POST", f"/channels/{channel_id}/messages", {"content": content})


def _origin_notice():
    runtime = Path(os.environ.get("INTEROP_RUNTIME", "~/.hermes/interop_runtime")).expanduser()
    sys.path.insert(0, str(runtime))
    from automation.interop import origin_notice  # noqa: PLC0415

    return origin_notice


def _thread_transport(channel_id: str):
    runtime = Path(os.environ.get("INTEROP_RUNTIME", "~/.hermes/interop_runtime")).expanduser()
    sys.path.insert(0, str(runtime))
    from automation.interop.discord_transport import DiscordTransport  # noqa: PLC0415

    return DiscordTransport(token=bot_token(), channel_id=channel_id)


def notify_result(draft: DraftRecord, content: str) -> object:
    """Route an execution/cancellation result: origin thread first, owner fallback.

    라우팅·폴백·NOTIFY-THREAD-FAIL 의미는 공유 구현
    `automation.interop.origin_notice.deliver`가 소유한다(2026-08-23 전 스킬 공통화).
    캘린더 내용(제목·시각·이벤트/캘린더 id)은 문구에도 스레드 이름에도 싣지 않는다 —
    SKILL.md 반출 금지 규칙에 따라 호출자가 draft id 만 담은 문구를 넘긴다.
    """
    try:
        origin_notice = _origin_notice()
    except ImportError as error:  # 낡은 interop 런타임/샌드박스 — 결과는 그래도 소유자에게 닿아야 한다
        print(
            f"NOTIFY-HELPER-MISSING draft={draft['id']} err={type(error).__name__}",
            file=sys.stderr,
        )
        return send_owner_dm(owner_id(), content)
    return origin_notice.deliver(
        api=_api,
        transport_factory=_thread_transport,
        record=draft,
        thread_name=f"캘린더 확정 (draft {draft['id']})",
        content=content,
        fallback=lambda body: send_owner_dm(owner_id(), body),
    )


def confirmation_message_content(entry: PendingConfirm) -> str:
    """Return the exact posted confirmation content for hash binding."""
    channel_id = calendar_binding.channel_for_entry(entry)
    message = _api("GET", f"/channels/{channel_id}/messages/{entry.dm_message_id}")
    return _required_string(message, "content", "확정 DM 내용이 없습니다")


def confirmation_reaction_users(
    entry: PendingConfirm, emoji: str
) -> tuple[dict[str, str | bool], ...]:
    """Read reaction users; Discord's absent-reaction response is empty."""
    channel_id = calendar_binding.channel_for_entry(entry)
    try:
        users = _api(
            "GET",
            f"/channels/{channel_id}/messages/{entry.dm_message_id}/reactions/"
            f"{quote(emoji, safe='')}?limit=100",
        )
    except HTTPError as error:
        if error.code == 404:
            return ()
        raise
    if not isinstance(users, list):
        raise GateError("확정 반응 응답이 올바르지 않습니다", 1)
    return tuple(user for user in users if isinstance(user, dict))


def reject_cancel_reaction(draft: DraftRecord) -> None:
    """Fail closed when the bound confirmation DM carries the owner's ⛔ reaction."""
    entry = _pending_entry(str(draft["id"]), required=False)
    if entry is None:
        return
    _validate_pending_binding(draft, entry)
    if _reaction_action(entry, owner_id()) == CANCEL_EMOJI:
        raise GateError("취소 반응이 있어 실행하지 않습니다", 1)


def confirm_via_reaction(draft: DraftRecord) -> str:
    """Authorize only the owner's ✅ on the exact pending confirmation DM."""
    entry = _pending_entry(str(draft["id"]))
    if entry is None:
        raise GateError("반응 확인용 pending confirm이 없습니다", 1)
    _validate_pending_binding(draft, entry)
    action = _reaction_action(entry, owner_id())
    if action == CANCEL_EMOJI:
        raise GateError("취소 반응이 있어 실행하지 않습니다", 1)
    if action != APPROVE_EMOJI:
        raise GateError("소유자 확정 반응이 없습니다", 1)
    return f"reaction:{entry.dm_message_id}"


def clear_pending(draft_id: str) -> None:
    """Remove the sole finished confirmation entry while preserving concurrent appends."""
    entry = _pending_entry(draft_id, required=False)
    if entry is not None:
        PendingConfirmStore().remove_completed((entry,), ())


def _change_summary(draft: DraftRecord) -> str:
    return calendar_core.render_change_summary(
        action=str(draft["action"]), summary=str(draft["summary"]), start=str(draft["start"]),
        end=str(draft["end"]), calendar_id=str(draft["calendar_id"]), event_id=str(draft["event_id"]),
    )


def _pending_entry(draft_id: str, *, required: bool = True) -> PendingConfirm | None:
    try:
        entries = [entry for entry in PendingConfirmStore().load() if entry.draft_id == draft_id]
    except PendingConfirmError as error:
        raise GateError("pending confirm store를 신뢰할 수 없습니다", 3) from error
    if len(entries) == 1:
        return entries[0]
    if not required and not entries:
        return None
    raise GateError("반응 확인용 pending confirm이 유일하지 않습니다", 1)


def _validate_pending_binding(draft: DraftRecord, entry: PendingConfirm) -> None:
    if draft.get("sha256") != entry.sha256:
        raise GateError("pending confirm 드래프트 해시 불일치", 1)
    if f"sha256:{entry.sha256}" not in confirmation_message_content(entry):
        raise GateError("확정 DM 드래프트 해시 불일치", 1)


def _reaction_action(entry: PendingConfirm, owner: str) -> str:
    cancel = _owner_reacted(confirmation_reaction_users(entry, CANCEL_EMOJI), owner)
    approve = _owner_reacted(confirmation_reaction_users(entry, APPROVE_EMOJI), owner)
    if cancel:
        return CANCEL_EMOJI
    if approve:
        return APPROVE_EMOJI
    return ""


def _owner_reacted(users: tuple[dict[str, str | bool], ...], owner: str) -> bool:
    return any(user.get("id", "") == owner and not bool(user.get("bot", False)) for user in users)


def _required_string(value: Any, key: str, message: str) -> str:
    if type(value) is not dict or not isinstance(result := value.get(key), str) or not result:
        raise GateError(message, 3)
    return result
