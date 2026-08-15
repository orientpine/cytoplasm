from __future__ import annotations

from .model import RelocationRecord

RENDER_VERSION = "mc-reloc-render-v1"
MAX_MESSAGE_CHARS = 1900


class RenderError(ValueError):
    pass


def render_relocation_approval(record: RelocationRecord, *, entry_text: str) -> str:
    content = (
        f"[memory→Obsidian 재배치 승인 | {RENDER_VERSION}]\n"
        "원본 메모리 항목\n"
        "───\n"
        f"{entry_text}\n"
        "───\n"
        f"- 대상 노트: `{record.note_relpath}`\n"
        f"- 회수 예상 문자 수: {record.reclaimable_chars}\n"
        f"- action_hash: `{record.action_hash}`\n\n"
        "승인(✅) 시 이 항목은 자체 메모리(ambient)에서 **삭제**되고 이후에는 "
        "**recall(검색)로만** 찾을 수 있게 됩니다 — 취소는 ⛔."
    )
    if len(content) > MAX_MESSAGE_CHARS:
        raise RenderError("memory relocation approval exceeds the postable length")
    return content
