"""Bind one Obsidian note push to the shared external-effect approval gate."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.interop.approval_lease import ApprovalLease, PostingJournal
from automation.interop.approval_lifecycle import (
    ApprovalGate,
    ApprovalIntent,
    Verdict,
    request_owner_approval,
)
from automation.interop.approval_surface import ApprovalKind
from automation.interop.external_effect_gate import (
    ApprovalContext,
    ExternalEffectDecision,
    ToolCall,
    evaluate_tool_call,
    load_denylist,
)

from .note import NotePlan

TOOL_NAME: Final = "obsidian_write.note_push"
KIND: Final = ApprovalKind.OBSIDIAN_WRITE


@dataclass(frozen=True, slots=True)
class OwnerApprovalRequest:
    """Existing lifecycle dependencies for posting one owner-approval request."""

    intent: ApprovalIntent
    gate: ApprovalGate
    lease: ApprovalLease
    journal: PostingJournal


def denylist_path() -> Path:
    override = os.environ.get("OBSIDIAN_WRITE_DENYLIST")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / "configs" / "external-effect-tools.yaml"


def build_tool_call(plan: NotePlan) -> ToolCall:
    return ToolCall(
        tool_name=TOOL_NAME,
        arguments={"body": plan.body, "relpath": plan.relpath.as_posix(), "title": plan.title},
    )


def approval_context() -> ApprovalContext:
    return ApprovalContext(approval_log=None, owner_id="", e2e_test_mode=False)


def evaluate(
    plan: NotePlan, *, context: ApprovalContext | None = None
) -> ExternalEffectDecision:
    ctx = context if context is not None else approval_context()
    return evaluate_tool_call(build_tool_call(plan), load_denylist(denylist_path()), ctx)


def request_approval(request: OwnerApprovalRequest) -> Verdict:
    """Use the shared lifecycle for a request whose channel was already resolved."""
    return request_owner_approval(request.intent, request.gate, request.lease, request.journal)
