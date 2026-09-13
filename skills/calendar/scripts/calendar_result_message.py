"""Calendar result envelopes contain only the producer's already-masked values."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage

_OUTCOMES: Final[dict[str, Literal["executed", "cancelled", "expired"]]] = {
    "done": "executed", "cancelled": "cancelled", "expired": "expired", "": "cancelled",
}
_ACTIONS: Final = {"create": "등록", "update": "수정", "delete": "삭제"}


def build(draft: Mapping[str, object], content: str, outcome: str) -> OwnerMessage | None:
    """선택 런타임이 없으면 기존 문자열로. 빈 outcome은 종결 표시 없는 고아 초안 폐기다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Result
        from automation.interop.origin_notice import approval_location
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    result = _OUTCOMES.get(outcome)
    if result is None:
        return None
    draft_id = str(draft["id"])
    card = draft.get("dm_message_id")
    return OwnerMessage(
        subject_key=draft_id,
        subject=f"캘린더 {_ACTIONS.get(str(draft.get('action', '')), '변경')}",
        fact=content,
        location=approval_location(
            draft, search=("Discord 검색", draft_id),
            message_id=card if isinstance(card, str) else None,
        ),
        owner=Action(verb="none"), agent_next=None, recovery="not_applicable",
        detail=Result(outcome=result),
    )
