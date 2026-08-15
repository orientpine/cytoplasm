"""Owner-approval transports for the budget mail gate (W4-3, 제약 1).

Same owner self-approval gate as W1-8/W0-7c, both paths fail-closed:
- production ``manual_reaction``: cha's own ✅ (send) or ⛔ (discard) reaction
  on the sanitized approval-request message, verified independently through
  Discord REST (reactor id == interop owner_id AND bot == false; bot/other
  reactions are rejected; ⛔ takes precedence);
- unattended ``signed_injection_e2e``: HMAC-signed injected event (W1-6
  adapter) accepted only under ``E2E_TEST_MODE=1`` (the production gateway
  refuses that env at boot).

The surface is decided ONCE per draft by ``budget_binding`` and persisted on the
record; every later read, reaction and delete takes its channel from that stored
binding, never from a fresh resolution made here.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from budget_gate import GateError, write_json

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
APPROVE_EMOJI = "\u2705"  # ✅
CANCEL_EMOJI = "\u26d4"
ENV_SECRETS = Path.home() / ".env.secrets"


def confirm_text(draft: dict) -> str:
    return f"APPROVE budget-mail:{draft['id']} sha256:{draft['sha256']} msg:{draft['message_id']}"


def owner_id() -> str:
    config = Path(os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")).expanduser()
    try:
        owner = json.loads(config.read_text(encoding="utf-8")).get("owner_id")
    except OSError:
        raise GateError(f"interop config 읽기 실패: {config}", 3) from None
    if not isinstance(owner, str) or not owner:
        raise GateError("interop config에 owner_id가 없습니다", 3)
    return owner



def bot_token() -> str:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if token:
        return token
    try:
        lines = ENV_SECRETS.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise GateError("DISCORD_BOT_TOKEN 없음 — 프로덕션 확인 경로 사용 불가", 3)


#: 배포 게이트(`automation/skill_gate.py`)와 같은 값. 소유자의 결정을 읽는 경로는
#: 429 를 존중해야 한다 — 2026-08-03 실측으로 이 게이트가 429 에 죽으면서 소유자가
#: 이미 누른 ⛔ 4건이 반영되지 않은 채 "아직 안 누름"과 구분되지 않았다.
_RATE_LIMIT_ATTEMPTS = 5
_RATE_LIMIT_FALLBACK_SECONDS = 1.0


def _retry_after(error: HTTPError) -> float:
    """Discord 가 요청한 대기 시간. 값이 없거나 읽을 수 없어도 백오프는 한다."""
    value = error.headers.get("Retry-After") if error.headers is not None else None
    try:
        return max(float(value), 0.0) if value is not None else _RATE_LIMIT_FALLBACK_SECONDS
    except (TypeError, ValueError):
        return _RATE_LIMIT_FALLBACK_SECONDS


def _send(request: Request) -> Any:
    with urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def _api(method: str, path: str, payload: dict | None = None) -> Any:
    request = Request(
        f"{API}{path}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {bot_token()}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method=method,
    )
    for _ in range(_RATE_LIMIT_ATTEMPTS - 1):
        try:
            return _send(request)
        except HTTPError as error:
            if error.code != 429:
                raise
            time.sleep(_retry_after(error))
    return _send(request)  # 마지막 시도는 실패해도 그대로 올린다


def post_approval_request(content: str, channel_id: str) -> str:
    message = _api("POST", f"/channels/{channel_id}/messages", {"content": content})
    return str(message["id"])


def add_reaction(message_id: str, emoji: str, channel_id: str) -> None:
    """Pre-add one Discord reaction to a posted approval request."""
    _api(
        "PUT",
        f"/channels/{channel_id}/messages/{message_id}"
        f"/reactions/{quote(emoji, safe='')}/@me",
    )


def delete_message(message_id: str, channel_id: str) -> None:
    """Delete one approval message from the channel it was actually posted to (SI-5)."""
    _api("DELETE", f"/channels/{channel_id}/messages/{message_id}")


def dm_owner(content: str) -> str:
    """Send a terse cancellation notice to the owner's direct-message channel."""
    import budget_binding

    channel_id = budget_binding.approval_directory().owner_dm()
    message = _api("POST", f"/channels/{channel_id}/messages", {"content": content})
    return str(message["id"])


def _owner_reacted(users: list[dict], owner: str) -> bool:
    return any(
        str(user.get("id", "")) == owner and not bool(user.get("bot", False))
        for user in users
    )


def _reaction_users(channel_id: str, message_id: str, emoji: str) -> list[dict]:
    try:
        users = _api(
            "GET",
            f"/channels/{channel_id}/messages/{message_id}"
            f"/reactions/{quote(emoji, safe='')}?limit=100",
        )
    except HTTPError as error:
        if error.code == 404:
            return []
        raise
    if not isinstance(users, list) or not all(isinstance(user, dict) for user in users):
        raise GateError("승인 리액션 응답이 유효하지 않음 — 거부", 1)
    return users


def resolve_reaction(draft: dict) -> str | None:
    """Return the bound owner decision, with ⛔ taking precedence over ✅."""
    import budget_binding

    if not draft.get("message_id"):
        raise GateError("드래프트가 아직 승인 요청으로 게시되지 않음 — 승인 불가", 1)
    channel_id = str(budget_binding.stored_binding(draft).channel_id)
    message = _api("GET", f"/channels/{channel_id}/messages/{draft['message_id']}")
    if not isinstance(message, dict) or draft["sha256"] not in str(message.get("content", "")):
        raise GateError("승인 메시지가 이 드래프트 해시를 참조하지 않음 — 거부", 1)
    owner = owner_id()
    if _owner_reacted(_reaction_users(channel_id, draft["message_id"], CANCEL_EMOJI), owner):
        return CANCEL_EMOJI
    if _owner_reacted(_reaction_users(channel_id, draft["message_id"], APPROVE_EMOJI), owner):
        return APPROVE_EMOJI
    return None


def confirm_via_reaction(draft: dict) -> str:
    """Return a bound owner-only ✅ approval reference or reject the draft."""
    action = resolve_reaction(draft)
    if action == APPROVE_EMOJI:
        return f"reaction:{draft['message_id']}"
    if action == CANCEL_EMOJI:
        raise GateError(f"소유자의 {CANCEL_EMOJI} 리액션으로 취소됨 — 발송하지 않습니다", 1)
    raise GateError(f"소유자의 {APPROVE_EMOJI} 리액션 없음 — 발송하지 않습니다", 1)


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


def _persisted_injection_channel_id(draft: dict) -> str:
    import budget_binding

    channel_id = budget_binding.persisted_channel_id(draft)
    if channel_id is None:
        raise GateError("주입 승인에는 저장된 승인 바인딩이 필요함 — 거부", 1)
    return channel_id


def confirm_via_injection(draft: dict, injection_path: Path) -> str:
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
    if event.channel_id != _persisted_injection_channel_id(draft):
        raise GateError("주입 승인 채널이 이 드래프트의 승인 표면이 아님 — 거부", 1)
    if event.text != confirm_text(draft):
        raise GateError("주입 승인 텍스트/해시 불일치 — 거부", 1)
    return f"injected:{event.event_id}"


def sign_injection(
    draft: dict, out_path: Path, user_id: str | None, channel_id: str | None, forge_signature: bool
) -> None:
    secret = _require_e2e_secret()
    adapter = _adapter()
    event = adapter.InboundEvent(
        event_id=str(uuid.uuid4()),
        user_id=user_id or owner_id(),
        channel_id=channel_id or _persisted_injection_channel_id(draft),
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
