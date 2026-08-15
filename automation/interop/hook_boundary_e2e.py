"""Exercise signed injection at the real pre_gateway_dispatch callback boundary."""

from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from automation.interop.injection_adapter import InboundEvent, sign_event


def main() -> None:
    """Prove signed owner control, paused veto, resume, refusal, and forged veto."""
    plugin = _plugin()
    secret = os.environ["INTEROP_E2E_SECRET"].encode("utf-8")
    config = json.loads(Path("~/.hermes/interop/config.json").expanduser().read_text(encoding="utf-8"))
    owner_id = config["owner_id"]
    state = Path("~/.hermes/interop/paused").expanduser()
    state.unlink(missing_ok=True)

    paused = plugin.pre_gateway_dispatch(_event(_envelope(owner_id, "!pause-agents", secret)), None, None)
    paused_followup = plugin.pre_gateway_dispatch(_event("ordinary inbound"), None, None)
    resumed = plugin.pre_gateway_dispatch(_event(_envelope(owner_id, "!resume-agents", secret)), None, None)
    refused = plugin.pre_gateway_dispatch(_event(_envelope("peer-bot", "!pause-agents", secret)), None, None)
    forged = plugin.pre_gateway_dispatch(_event(_envelope(owner_id, "ping", secret, forged=True)), None, None)
    print(
        json.dumps(
            {
                "pause_skip": paused["action"] == "skip",
                "paused_followup_skip": paused_followup["action"] == "skip",
                "resume_skip": resumed["action"] == "skip",
                "state_cleared": not state.exists(),
                "non_owner_allowed": refused["action"] == "allow",
                "forged_skip": forged["action"] == "skip",
            }
        )
    )


def _event(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        source=SimpleNamespace(user_id="transport-user", is_bot=False, thread_id="e2e", chat_id="e2e"),
    )


def _envelope(user_id: str, text: str, secret: bytes, *, forged: bool = False) -> str:
    event = InboundEvent(event_id="e2e-event", user_id=user_id, channel_id="e2e", text=text)
    signature = "0" * 64 if forged else sign_event(event, secret)
    return json.dumps({"event": asdict(event), "signature": signature})


def _plugin():
    path = Path("~/.hermes/plugins/interop-protocol/__init__.py").expanduser()
    spec = importlib.util.spec_from_file_location("interop_protocol_e2e", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("interop plugin load failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    main()
