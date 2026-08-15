from __future__ import annotations

import json

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


def test_denylist_when_gws_gmail_send_then_requires_approval() -> None:
    # Given
    rules = load_denylist(_denylist_path())
    call = ToolCall(tool_name="terminal", arguments={"command": "gws gmail +send --to owner@example.invalid"})

    # When
    decision = evaluate_tool_call(call, rules, ApprovalContext(None, "owner", False))

    # Then
    assert decision.external_effect
    assert not decision.allowed
    assert decision.reason == "approval_required"
    assert decision.action_hash.startswith("sha256:")


def test_denylist_when_calendar_list_is_read_only_then_allows() -> None:
    # Given
    rules = load_denylist(_denylist_path())
    call = ToolCall(tool_name="terminal", arguments={"command": "gws calendar events list --calendar primary"})

    # When
    decision = evaluate_tool_call(call, rules, ApprovalContext(None, "owner", False))

    # Then
    assert not decision.external_effect
    assert decision.allowed
    assert decision.reason is None


def test_approval_when_signed_e2e_owner_record_matches_call_then_allows(tmp_path) -> None:
    # Given
    rules = load_denylist(_denylist_path())
    call = ToolCall(tool_name="terminal", arguments={"command": "gws calendar events insert --json '{} '"})
    context = ApprovalContext(tmp_path / "approvals.jsonl", "owner", True)
    blocked = evaluate_tool_call(call, rules, context)
    event = InboundEvent(
        event_id="e2e-approval",
        user_id="owner",
        channel_id="approvals",
        text=approval_challenge(blocked.action_hash, blocked.target_id),
    )
    secret = b"e2e-secret"

    # When
    recorded = record_signed_e2e_approval(
        context,
        ApprovalBinding(blocked.action_hash, blocked.target_id),
        SignedApprovalEvent(event, sign_event(event, secret), secret),
    )
    allowed = evaluate_tool_call(call, rules, context)

    # Then
    assert recorded
    assert allowed.external_effect
    assert allowed.allowed
    assert allowed.reason == "approved"


def test_approval_when_signed_e2e_record_is_reused_in_production_then_blocks(tmp_path) -> None:
    # Given
    rules = load_denylist(_denylist_path())
    call = ToolCall(tool_name="terminal", arguments={"command": "gws gmail +send --to owner@example.invalid"})
    context = ApprovalContext(tmp_path / "approvals.jsonl", "owner", True)
    blocked = evaluate_tool_call(call, rules, context)
    event = InboundEvent(
        event_id="e2e-approval",
        user_id="owner",
        channel_id="approvals",
        text=approval_challenge(blocked.action_hash, blocked.target_id),
    )
    secret = b"e2e-secret"
    assert record_signed_e2e_approval(
        context,
        ApprovalBinding(blocked.action_hash, blocked.target_id),
        SignedApprovalEvent(event, sign_event(event, secret), secret),
    )

    # When
    decision = evaluate_tool_call(call, rules, ApprovalContext(tmp_path / "approvals.jsonl", "owner", False))

    # Then
    assert not decision.allowed
    assert decision.reason == "approval_required"


def test_approval_when_manual_owner_record_matches_then_allows_in_production(tmp_path) -> None:
    # Given
    rules = load_denylist(_denylist_path())
    call = ToolCall(tool_name="external_submit", arguments={"payload": "dry-run"})
    context = ApprovalContext(tmp_path / "approvals.jsonl", "owner", False)
    blocked = evaluate_tool_call(call, rules, context)
    record = {
        "action": "external_effect.approval",
        "approval": {"channel": "approvals", "message_id": "masked", "method": "manual_reaction", "owner_id": "owner"},
        "hash": blocked.action_hash,
        "result": {"status": "approved"},
        "target_id": blocked.target_id,
        "timestamp": "2026-07-15T00:00:00Z",
    }
    (tmp_path / "approvals.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    # When
    allowed = evaluate_tool_call(call, rules, context)

    # Then
    assert allowed.allowed
    assert allowed.reason == "approved"


def test_denylist_when_raw_gws_drive_upload_of_patent_draft_then_requires_approval() -> None:
    # Given — a raw gws drive upload touching a patent-drafts path (an LLM bypass of the skill CLI)
    rules = load_denylist(_denylist_path())
    call = ToolCall(
        tool_name="terminal",
        arguments={"command": "gws drive +upload <your-home-directory>/patent-drafts/demo/draft.md"},
    )

    # When
    decision = evaluate_tool_call(call, rules, ApprovalContext(None, "owner", False))

    # Then
    assert decision.external_effect
    assert not decision.allowed
    assert decision.reason == "approval_required"
    assert decision.action_hash.startswith("sha256:")


def test_denylist_when_raw_gws_drive_files_create_of_patent_draft_then_requires_approval() -> None:
    # Given — the files-create branch of the backstop must also be caught
    rules = load_denylist(_denylist_path())
    call = ToolCall(
        tool_name="bash",
        arguments={"command": "gws drive files create --json '{} ' --upload ~/patent-drafts/demo/draft.md.age"},
    )

    # When
    decision = evaluate_tool_call(call, rules, ApprovalContext(None, "owner", False))

    # Then
    assert decision.external_effect
    assert not decision.allowed
    assert decision.reason == "approval_required"


def test_denylist_when_gws_drive_upload_outside_patent_drafts_then_allows() -> None:
    # Given — an ordinary own-Drive upload (e.g. procurement) must NOT be caught by the backstop
    rules = load_denylist(_denylist_path())
    call = ToolCall(
        tool_name="terminal",
        arguments={"command": "gws drive +upload /tmp/procure-review.hwpx"},
    )

    # When
    decision = evaluate_tool_call(call, rules, ApprovalContext(None, "owner", False))

    # Then
    assert not decision.external_effect
    assert decision.allowed
    assert decision.reason is None


def _denylist_path() -> str:
    return "configs/external-effect-tools.yaml"
