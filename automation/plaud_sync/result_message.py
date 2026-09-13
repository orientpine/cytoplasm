"""Plaud 실행 결과 봉투. 노트 본문은 읽지 않고 기존 식별자와 사실만 옮긴다."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .model import PlaudSyncRecord

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage


def result_notice_message(
    record: PlaudSyncRecord, content: str, outcome: str,
) -> OwnerMessage | None:
    """FSM이 실제로 통지하는 written/abandoned만 종결 봉투로 만든다."""
    try:
        from automation.interop.origin_notice import approval_location
        from automation.interop.owner_message import Action, OwnerMessage, Result
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    match outcome:
        case "written":
            detail = Result(outcome="executed")
            subject = record.note_relpath
        case "abandoned":
            detail = Result(outcome="cancelled")
            subject = record.recording_id
        case _:
            # 중간 ACK·다른 호출자의 상태어는 기존 문구/종결 판정을 그대로 둔다.
            return None
    return OwnerMessage(
        subject_key=record.recording_id, subject=subject, fact=content,
        location=approval_location({
            "approval_guild_id": record.approval_guild_id,
            "approval_thread_id": record.approval_thread_id,
            "message_id": record.message_id,
        }, search=("Discord 검색", record.recording_id)),
        owner=Action(verb="none"), agent_next=None, recovery="not_applicable", detail=detail,
    )
