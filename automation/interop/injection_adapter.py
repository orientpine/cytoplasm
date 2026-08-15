"""HMAC verification for opt-in E2E synthetic inbound events."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class InboundEvent:
    """The minimal trusted shape passed from the E2E injection boundary."""

    event_id: str
    user_id: str
    channel_id: str
    text: str


def sign_event(event: InboundEvent, secret: bytes) -> str:
    """Return a deterministic SHA-256 HMAC over the event's canonical JSON."""
    return hmac.new(secret, _canonical_event(event), hashlib.sha256).hexdigest()


def verify_signed_event(event: InboundEvent, signature: str, secret: bytes) -> bool:
    """Return true only for a constant-time matching event signature."""
    return hmac.compare_digest(sign_event(event, secret), signature)


def accept_test_event(
    event: InboundEvent,
    signature: str,
    secret: bytes,
    *,
    e2e_test_mode: bool,
) -> bool:
    """Accept a synthetic event only in explicitly enabled E2E mode."""
    return e2e_test_mode and verify_signed_event(event, signature, secret)


def _canonical_event(event: InboundEvent) -> bytes:
    return json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
