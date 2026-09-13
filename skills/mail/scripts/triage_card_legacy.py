"""Frozen v1 mail approval bytes, including all destination masking branches."""
from __future__ import annotations

from enum import StrEnum
from typing import assert_never


class ApprovalRenderDestination(StrEnum):
    """The output surface that determines sensitive reply disclosure."""

    CONSOLE = "console"
    OWNER_DM = "owner-dm"


def render_approvals_message(
    draft: dict,
    *,
    destination: ApprovalRenderDestination = ApprovalRenderDestination.CONSOLE,
    instruction: str = "",
) -> str:
    """Frozen v1. Sensitive bodies are disclosed only to OWNER_DM."""
    if draft.get("provider") == "gmail":
        lines = [
            "[mail-triage] Gmail 발송 승인 요청 (DM 확정)",
            f"- 발신 계정: `{draft['sender_account']}`",
            f"- 작업: `{draft['gmail_approval_snapshot']['action_kind']}`",
            f"- 수신자: `{draft['to']}`",
        ]
        if draft.get("cc"):
            lines.append(f"- Cc: `{draft['cc']}`")
        lines.extend([
            f"- 회신 대상: `{draft['reply_target'] or '-'}`",
            f"- 제목: `{draft['subject']}`",
            "- 본문:",
            "```",
            draft["body"],
            "```",
        ])
    elif draft.get("kind") == "compose":
        lines = [
            "[mail-triage] 새 메일 발송 승인 요청 (DM 확정)",
            f"- To: `{draft['to']}`",
        ]
        if draft.get("cc"):
            lines.append(f"- Cc: `{draft['cc']}`")
        lines.extend([
            f"- 제목: `{draft['subject']}`",
            "- 본문:",
            "```",
            draft["body"],
            "```",
        ])
    elif draft["sensitive"]:
        lines = [
            "[mail-triage] 민감 메일 회신 발송 승인 요청",
            f"- 유형: {draft['category']} / 태그: {', '.join(draft['tags']) or '-'}"
            f" / 플래그: {', '.join(draft['flags']) or '-'}",
            f"- 발신(마스킹): `{draft['sender_masked']}`",
            f"- 메일(불투명 id): `{draft['uid_opaque']}`",
        ]
        match destination:
            case ApprovalRenderDestination.OWNER_DM:
                if draft.get("cc"):
                    lines.append(f"- Cc: `{draft['cc']}`")
                lines.extend([
                    f"- 회신 제목: {draft['subject']}",
                    "- 회신 본문:",
                    "```",
                    draft["body"],
                    "```",
                ])
            case ApprovalRenderDestination.CONSOLE:
                pass
            case unreachable:
                assert_never(unreachable)
    else:
        preview = draft["body"][:600]
        lines = [
            "[mail-triage] 수신메일 회신 발송 승인 요청",
            f"- 분류: {draft['category']} / 플래그: {', '.join(draft['flags']) or '-'}",
            f"- 발신(마스킹): `{draft['sender_masked']}`",
            f"- 원문 제목: {draft['mail_subject']}",
        ]
        if draft.get("cc"):
            lines.append(f"- Cc: `{draft['cc']}`")
        lines.extend([
            f"- 회신 제목: {draft['subject']}",
            "- 회신 본문:",
            "```",
            preview + ("…" if len(draft["body"]) > 600 else ""),
            "```",
        ])
    if draft.get("quote"):
        lines.append("- 원문 인용: 포함 (수신 메일 원문이 발송 본문 하단에 붙습니다)")
    attachments = draft.get("attachments") or []
    if attachments:
        if draft.get("provider") == "gmail":
            lines.append(f"- 첨부: {len(attachments)}개")
            for item in attachments:
                safe_name = str(item["display_name"]).replace("`", "'")
                lines.append(
                    f"  - `{safe_name}` · {item['size_bytes']} bytes · `{item['sha256']}`"
                )
        elif draft.get("sensitive") and draft.get("kind") != "compose":
            lines.append(f"- 첨부: {len(attachments)}개")
        else:
            lines.append(f"- 첨부: {len(attachments)}개")
            for item in attachments:
                safe_name = str(item["display_name"]).replace("`", "'")
                lines.append(
                    f"  - `{safe_name}` · {item['size_bytes']} bytes · `{item['mime_type']}`"
                )
    lines.append(f"- draft: `{draft['id']}` sha256: `{draft['sha256']}`")
    if draft.get("provider") == "gmail":
        lines.append(f"- action hash: `{draft['approval_action_hash']}`")
    if instruction:
        lines.append(f"- 반응(기본): {instruction}")
    return "\n".join(lines)
