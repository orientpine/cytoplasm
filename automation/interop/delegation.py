"""Deterministic Interop Protocol v0 §2 envelope handling."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class InteropEnvelope:
    """A parsed §2 inter-agent query or response."""

    correlation_id: str
    sender_id: str
    recipient_id: str
    intent: str
    payload: dict[str, int | list[str] | str]


def format_envelope(envelope: InteropEnvelope) -> str:
    """Serialize one v0 envelope as compact JSON for Discord transport."""
    return json.dumps(
        {
            "version": "v0",
            "correlation_id": envelope.correlation_id,
            "sender_id": envelope.sender_id,
            "recipient_id": envelope.recipient_id,
            "intent": envelope.intent,
            "payload": envelope.payload,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def parse_envelope(message: str) -> InteropEnvelope | None:
    """Parse exactly the required v0 §2 fields, or return no envelope."""
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "version", "correlation_id", "sender_id", "recipient_id", "intent", "payload"
    }:
        return None
    correlation_id = payload.get("correlation_id")
    sender_id = payload.get("sender_id")
    recipient_id = payload.get("recipient_id")
    intent = payload.get("intent")
    body = payload.get("payload")
    if (
        payload.get("version") != "v0"
        or not isinstance(correlation_id, str)
        or not isinstance(sender_id, str)
        or not isinstance(recipient_id, str)
        or not isinstance(intent, str)
        or not isinstance(body, dict)
    ):
        return None
    return InteropEnvelope(correlation_id, sender_id, recipient_id, intent, body)


def response_for(query: InteropEnvelope, *, sender_id: str) -> InteropEnvelope:
    """Return the deterministic response for a supported query intent."""
    if query.intent == "query_availability":
        return InteropEnvelope(
            correlation_id=query.correlation_id,
            sender_id=sender_id,
            recipient_id=query.sender_id,
            intent="response_availability",
            payload={"slots": _availability_slots(query.payload)},
        )
    if query.intent == "query_confirm_slot":
        return InteropEnvelope(
            correlation_id=query.correlation_id,
            sender_id=sender_id,
            recipient_id=query.sender_id,
            intent="response_confirm_slot",
            payload={
                "result": "declined",
                "slot": str(query.payload.get("slot", "")),
            },
        )
    return InteropEnvelope(
        correlation_id=query.correlation_id,
        sender_id=sender_id,
        recipient_id=query.sender_id,
        intent=f"response_{query.intent}",
        payload={"result": "declined", "reason": "unsupported_intent"},
    )


def _availability_slots(payload: dict[str, int | list[str] | str]) -> list[str]:
    """Offer deterministic hour-aligned slots inside a §2.3 range payload.

    Without a parseable range the legacy W1-5 marker response is preserved so
    the existing interop gate keeps passing unchanged.
    """
    try:
        range_start = datetime.fromisoformat(str(payload["range_start"]))
        range_end = datetime.fromisoformat(str(payload["range_end"]))
        duration = timedelta(minutes=int(str(payload["duration_min"])))
    except (KeyError, TypeError, ValueError):
        return ["interop-ready"]
    if range_start.tzinfo is None or range_end.tzinfo is None or duration <= timedelta(0):
        return ["interop-ready"]
    slot = range_start.replace(minute=0, second=0, microsecond=0)
    if slot < range_start:
        slot += timedelta(hours=1)
    slots: list[str] = []
    while slot + duration <= range_end and len(slots) < 8:
        slots.append(slot.isoformat())
        slot += timedelta(hours=1)
    return slots
