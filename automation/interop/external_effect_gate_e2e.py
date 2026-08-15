"""Run isolated live-hook regressions without invoking an external tool."""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from automation.interop.external_effect_gate import (
    ApprovalBinding,
    ApprovalContext,
    SignedApprovalEvent,
    ToolCall,
    approval_challenge,
    evaluate_tool_call,
    load_denylist,
    record_signed_e2e_approval,
)
from automation.interop.injection_adapter import InboundEvent, sign_event
from automation.interop.report import ReportStatus, TaskReport, format_report


def main() -> None:
    """Assert block, approved dry-run, chatter attenuation, and report flow."""
    plugin = _plugin()
    approval_log = Path(os.environ["EXTERNAL_EFFECT_APPROVAL_LOG"])
    owner_id = _config()["owner_id"]
    secret = os.environ["INTEROP_E2E_SECRET"].encode("utf-8")
    call = ToolCall("terminal", {"command": "gws gmail +send --to masked@example.invalid"})
    rules = load_denylist(os.environ["EXTERNAL_EFFECT_DENYLIST_PATH"])
    blocked = plugin.pre_tool_call(call.tool_name, call.arguments)
    context = ApprovalContext(approval_log=approval_log, owner_id=owner_id, e2e_test_mode=True)
    decision = evaluate_tool_call(call, rules, context)
    event = InboundEvent(
        event_id="e2e-external-effect-approval",
        user_id=owner_id,
        channel_id="approvals",
        text=approval_challenge(decision.action_hash, decision.target_id),
    )
    recorded = record_signed_e2e_approval(
        context,
        ApprovalBinding(decision.action_hash, decision.target_id),
        SignedApprovalEvent(event, sign_event(event, secret), secret),
    )
    allowed = plugin.pre_tool_call(call.tool_name, call.arguments)
    chatter = [plugin.pre_gateway_dispatch(_event(f"🟢 {index}"), None, None) for index in range(1, 4)]
    report = format_report(
        TaskReport(
            agent_id="peer-test",
            task_id="e2e-interop-report",
            status=ReportStatus.DONE,
            summary="controlled report",
            links=(),
            timestamp=datetime.now(UTC),
        )
    )
    protocol = plugin.pre_gateway_dispatch(_event(report), None, None)
    print(
        json.dumps(
            {
                "approved_dry_run_reached": allowed is None,
                "approval_recorded": recorded,
                "blocked_without_approval": isinstance(blocked, dict) and blocked.get("action") == "block",
                "low_value_suppressed": chatter[-1].get("action") == "skip" and chatter[-1].get("reason") == "interop_low_value_chatter",
                "no_tool_executed": True,
                "protocol_report_allowed": protocol.get("action") == "allow",
            },
            sort_keys=True,
        )
    )


def _config() -> dict[str, str]:
    payload = json.loads(Path("~/.hermes/interop/config.json").expanduser().read_text(encoding="utf-8"))
    owner_id = payload.get("owner_id")
    if not isinstance(owner_id, str):
        raise RuntimeError("isolated config is missing owner_id")
    return {"owner_id": owner_id}


def _event(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        source=SimpleNamespace(user_id="peer-test", is_bot=True, thread_id="controlled-thread", chat_id="controlled-chat"),
    )


def _plugin():
    path = Path(os.environ["INTEROP_PLUGIN_PATH"])
    spec = importlib.util.spec_from_file_location("interop_protocol_external_effect_e2e", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("isolated plugin load failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    main()
