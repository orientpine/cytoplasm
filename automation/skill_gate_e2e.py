"""Signed owner-injection helpers isolated from the production peer gate."""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from automation.interop.injection_adapter import InboundEvent, accept_test_event, sign_event


@dataclass(frozen=True, slots=True)
class GateBindings:
    owner_id: str
    channel_id: str
    approval_text: Callable[[str, str, str], str]
    log_approval: Callable[[str, str, str, str], None]
    mask: Callable[[str], str]


def check_injected(args: Any, bindings: GateBindings) -> int:
    """Verify the E2E-only owner injection; peer attestation is checked elsewhere."""
    secret = os.environ.get("INTEROP_E2E_SECRET", "")
    if not secret:
        print("REJECTED: E2E mode without INTEROP_E2E_SECRET", file=sys.stderr)
        return 1
    envelope = json.loads(Path(args.injection_file).read_text(encoding="utf-8"))
    event = InboundEvent(
        event_id=str(envelope["event"]["event_id"]),
        user_id=str(envelope["event"]["user_id"]),
        channel_id=str(envelope["event"]["channel_id"]),
        text=str(envelope["event"]["text"]),
    )
    if not accept_test_event(event, str(envelope["signature"]), secret.encode("utf-8"), e2e_test_mode=True):
        print("REJECTED: injected approval signature invalid", file=sys.stderr)
        return 1
    if event.user_id != bindings.owner_id:
        print(f"REJECTED: injected approval from non-owner {bindings.mask(event.user_id)}", file=sys.stderr)
        return 1
    if event.channel_id != bindings.channel_id or event.text != bindings.approval_text(args.skill, args.hash, args.message_id):
        print("REJECTED: injected approval channel/text mismatch", file=sys.stderr)
        return 1
    bindings.log_approval(args.skill, args.hash, args.message_id, "signed_injection_e2e")
    print(f"APPROVED method=signed_injection_e2e owner={bindings.mask(bindings.owner_id)}")
    return 0


def sign(args: Any, bindings: GateBindings) -> int:
    """Create a signed owner injection only when the E2E mode is explicit."""
    if os.environ.get("E2E_TEST_MODE") != "1":
        print("FATAL: sign is E2E-only (set E2E_TEST_MODE=1)", file=sys.stderr)
        return 2
    secret = os.environ.get("INTEROP_E2E_SECRET", "")
    if not secret:
        print("FATAL: INTEROP_E2E_SECRET missing", file=sys.stderr)
        return 2
    user_id = args.user_id or bindings.owner_id
    event = InboundEvent(str(uuid.uuid4()), user_id, bindings.channel_id, bindings.approval_text(args.skill, args.hash, args.message_id))
    signature = "0" * 64 if args.forge_signature else sign_event(event, secret.encode("utf-8"))
    output = Path(args.out)
    output.write_text(json.dumps({"event": asdict(event), "signature": signature}, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    output.chmod(0o600)
    print(f"signed injection written user={bindings.mask(user_id)} forged={args.forge_signature}")
    return 0
