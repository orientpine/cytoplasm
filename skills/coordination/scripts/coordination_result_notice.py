"""조율 종결 통지 봉투: 사실 문구와 승인 카드 좌표만 옮긴다."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage


def result_message(record: dict[str, str], content: str, outcome: str) -> OwnerMessage | None:
    """옛 런타임과 중간 통지는 기존 문자열로 보낸다. 취소·만료는 제목을 추가하지 않는다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Result
        from automation.interop.origin_notice import approval_location
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    subject_key = record.get("id", "")
    subject = subject_key
    match outcome:
        case "done":
            detail = Result(outcome="executed")
            subject = record.get("summary", subject_key)
        case "cancelled":
            detail = Result(outcome="cancelled")
        case "expired":
            detail = Result(outcome="expired")
        case _:
            return None  # outcome은 개방형 문자열이며 중간 통지는 종결이 아니다.
    return OwnerMessage(
        subject_key=subject_key, subject=subject, fact=content,
        location=approval_location(
            record, search=("Discord 검색", subject_key),
            message_id=record.get("dm_message_id", ""),
        ),
        owner=Action(verb="none"), agent_next=None,
        recovery="not_applicable", detail=detail,
    )
