"""Mail card envelope facts come only from the frozen destination-masked view."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage


def message(draft_id: str, legacy: str, instruction: str) -> OwnerMessage:
    from automation.interop.owner_message import Action, Approval, OwnerMessage, Ref
    from automation.interop.approval_surface import ApprovalKind, reaction_instruction, required_surface

    lines = legacy.splitlines()
    binding_lines = 2 if lines[-1].startswith("- action hash:") else 1
    kind = ApprovalKind.MAIL_REPLY
    owner_instruction = instruction or reaction_instruction(kind, required_surface(kind))
    facts = " · ".join(line for line in lines[1:-binding_lines] if line != "```")
    here = Ref(scope="self")
    return OwnerMessage(
        subject_key=draft_id, subject=lines[0].removeprefix("[mail-triage] "),
        fact=facts, location=here, owner=Action("react", here, owner_instruction),
        agent_next="승인된 메일만 발송", recovery="irreversible",
        detail=Approval(None, "메일을 발송하지 않음"),
    )
