"""Mail card envelope facts come only from the frozen destination-masked view."""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage


def message(
    draft_id: str, legacy: str, instruction: str, *,
    render_version: Literal["owner-ko-v1", "owner-ko-v2"] = "owner-ko-v1",
) -> OwnerMessage:
    """Select presentation separately from the three frozen card inputs."""
    from automation.interop.owner_message import Action, Approval, OwnerMessage, Ref
    from automation.interop.approval_surface import ApprovalKind, reaction_instruction, required_surface

    lines = legacy.splitlines()
    binding_lines = 2 if lines[-1].startswith("- action hash:") else 1
    kind = ApprovalKind.MAIL_REPLY
    owner_instruction = instruction or reaction_instruction(kind, required_surface(kind))
    match render_version:
        case "owner-ko-v1":
            facts = " · ".join(line for line in lines[1:-binding_lines] if line != "```")
        case "owner-ko-v2":
            facts = "\n".join(lines[1:-binding_lines])
        case _:
            assert_never(render_version)
    here = Ref(scope="self")
    return OwnerMessage(
        subject_key=draft_id, subject=lines[0].removeprefix("[mail-triage] "),
        fact=facts, location=here, owner=Action("react", here, owner_instruction),
        agent_next="승인된 메일만 발송", recovery="irreversible",
        detail=Approval(None, "메일을 발송하지 않음"),
        render_version=render_version,
    )
