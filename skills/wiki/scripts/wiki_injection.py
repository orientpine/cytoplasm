"""위키 주입 승인 프로토콜. 운영 게이트의 서명·신원·바인딩 판정을 유지한다."""
from __future__ import annotations

from importlib import import_module

import json
import os
import uuid
from pathlib import Path


def _require_e2e_secret() -> str:
    gate = import_module("wiki_gate")
    if os.environ.get("E2E_TEST_MODE") != "1":
        raise gate.GateError("주입 경로는 E2E_TEST_MODE=1 전용입니다 (프로덕션 게이트웨이 금지)", 1)
    secret = os.environ.get("INTEROP_E2E_SECRET", "")
    if not secret:
        raise gate.GateError("INTEROP_E2E_SECRET 누락", 1)
    return secret


def _persisted_injection_channel_id(draft: dict) -> str:
    gate = import_module("wiki_gate")
    import wiki_binding

    channel_id = wiki_binding.persisted_channel_id(draft)
    if channel_id is None:
        raise gate.GateError("주입 승인에는 저장된 승인 바인딩이 필요함 — 거부", 1)
    return channel_id


def confirm_via_injection(draft: dict, injection_path: Path) -> str:
    gate = import_module("wiki_gate")
    secret = gate._require_e2e_secret()
    adapter = gate._adapter()
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
        raise gate.GateError("주입 승인 서명 불일치 — 거부", 1)
    if event.user_id != gate.owner_id():
        raise gate.GateError("주입 승인 발신자가 소유자가 아님 — 거부", 1)
    if event.channel_id != gate._persisted_injection_channel_id(draft):
        raise gate.GateError("주입 승인 채널 불일치 — 거부", 1)
    if event.text != gate.confirm_text(draft):
        raise gate.GateError("주입 승인 텍스트/해시 불일치 — 거부", 1)
    reaction_emoji = envelope["event"].get("reaction_emoji")
    if reaction_emoji is not None:
        if reaction_emoji == gate.APPROVE_EMOJI:
            return "injected-reaction:approve"
        if reaction_emoji == gate.CANCEL_EMOJI:
            raise gate.GateError("주입 취소 리액션으로 취소됨 — 저장하지 않습니다", 1)
        raise gate.GateError("주입 리액션 값이 승인/취소가 아님 — 거부", 1)
    return f"injected:{event.event_id}"


def sign_injection(
    draft: dict,
    out_path: Path,
    user_id: str | None,
    channel_id: str | None,
    forge_signature: bool,
    reaction_emoji: str | None = None,
) -> None:
    gate = import_module("wiki_gate")
    secret = gate._require_e2e_secret()
    adapter = gate._adapter()
    event = adapter.InboundEvent(
        event_id=str(uuid.uuid4()),
        user_id=user_id or gate.owner_id(),
        channel_id=channel_id or gate._persisted_injection_channel_id(draft),
        text=gate.confirm_text(draft),
    )
    signature = "0" * 64 if forge_signature else adapter.sign_event(event, secret.encode("utf-8"))
    gate._write_json(
        out_path,
        {
            "event": {
                "event_id": event.event_id,
                "user_id": event.user_id,
                "channel_id": event.channel_id,
                "text": event.text,
                **({"reaction_emoji": reaction_emoji} if reaction_emoji is not None else {}),
            },
            "signature": signature,
        },
    )
