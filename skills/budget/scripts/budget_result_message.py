"""이미 마스킹한 budget 결과를 선택적 소유자 메시지 계약으로 옮긴다."""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeVar, assert_never

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage

_RecordValue = TypeVar("_RecordValue")


def result_message(
    draft: dict[str, _RecordValue], content: str, outcome: str,
) -> OwnerMessage | None:
    """실행 결과가 없는 ACK와 옛 런타임은 기존 문자열 경로를 유지한다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Result
        from automation.interop.origin_notice import approval_location
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    detail = {
        "DONE": Result(outcome="executed"),
        "CANCELLED": Result(outcome="cancelled"),
        "EXPIRED": Result(outcome="expired"),
    }.get(outcome)
    if detail is None:
        return None
    recovery: Literal["irreversible", "not_applicable"]
    match detail.outcome:
        case "executed":
            recovery = "irreversible"
        case "cancelled" | "expired":
            recovery = "not_applicable"
        case unreachable:
            assert_never(unreachable)
    return OwnerMessage(
        subject_key=str(draft["id"]), subject=str(draft["subject"]), fact=content,
        location=approval_location(draft, search=("Discord 검색", str(draft["id"]))),
        owner=Action(verb="none"), agent_next=None, recovery=recovery, detail=detail,
    )
