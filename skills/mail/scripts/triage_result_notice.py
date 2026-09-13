"""메일 종결 통지 봉투: 기존 사실 문구와 노출 범위를 그대로 옮긴다."""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage


def result_message(draft: dict[str, str], content: str, outcome: str) -> OwnerMessage | None:
    """선택 런타임 경계. 옛 모듈 또는 중간 통지는 기존 문자열로 배달한다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Result
        from automation.interop.origin_notice import approval_location
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    recovery: Literal["irreversible", "not_applicable"] = "not_applicable"
    match outcome:
        case "DONE":
            detail = Result(outcome="executed")
            recovery = "irreversible"
        case "CANCELLED":
            detail = Result(outcome="cancelled")
        case "EXPIRED":
            detail = Result(outcome="expired")
        case _:
            return None  # outcome은 개방형 문자열이며 중간 통지는 종결이 아니다.
    return OwnerMessage(
        subject_key=draft["id"], subject=draft["subject"], fact=content,
        location=approval_location(draft, search=("Discord 검색", draft["id"])),
        owner=Action(verb="none"), agent_next=None, recovery=recovery, detail=detail,
    )
