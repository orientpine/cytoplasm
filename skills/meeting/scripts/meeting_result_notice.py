"""회의 처리 완료 봉투: 전사본 없이 기존 마스킹된 통지와 식별자만 받는다."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage


@dataclass(frozen=True, slots=True)
class CompletedMeeting:
    """산출물 처리 뒤에만 생성하는 완료 식별자. subject는 생산자가 이미 마스킹한다."""

    ref: str
    subject: str


def result_message(
    record: dict[str, str], content: str, completed: CompletedMeeting | None,
) -> OwnerMessage | None:
    """미완료 또는 옛 런타임에는 기존 문자열 경로를 유지한다."""
    if completed is None:
        return None
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Result
        from automation.interop.origin_notice import approval_location
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    return OwnerMessage(
        subject_key=completed.ref, subject=completed.subject, fact=content,
        location=approval_location(record, search=("Discord 검색", completed.ref)),
        owner=Action(verb="none"), agent_next=None, recovery="not_applicable",
        detail=Result(outcome="executed"),
    )
