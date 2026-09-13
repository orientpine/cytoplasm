"""할 일 종결 통지: 기존 사실과 마스킹 범위만 선택적 봉투로 옮긴다."""
from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar, assert_never

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage

_RecordValue = TypeVar("_RecordValue")


def result_message(
    record: dict[str, _RecordValue], content: str, outcome: str,
) -> OwnerMessage | None:
    """옛 런타임과 실행 결과가 없는 중간 통지는 원래 문자열로 보낸다."""
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
    key = str(record.get("id") or "")
    match detail.outcome:
        case "executed" | "expired":
            subject = str(record.get("title") or key)
        case "cancelled":
            subject = key  # 기존 취소 통지는 제목 없이 승인 id만 노출한다.
        case unreachable:
            assert_never(unreachable)
    return OwnerMessage(
        subject_key=key, subject=subject, fact=content,
        location=approval_location(record, search=("Discord 검색", key)),
        owner=Action(verb="none"), agent_next=None,
        # 실행 경로는 task id만 보유한다. URL을 추측하거나 새 조회를 하지 않는다.
        recovery="not_applicable", detail=detail,
    )
