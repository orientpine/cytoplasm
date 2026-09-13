"""자체 스킬 감사 통지 봉투 — 원장·전송·시계는 소유하지 않는다."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage


def audit_message(content: str, account_label: str) -> OwnerMessage | None:
    """구간을 저장하지 않는 감사의 실행 결과. 계정 라벨은 호출자가 마스킹한다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    return OwnerMessage(
        subject_key=account_label, subject="[자체 스킬 감사]",
        fact=content.partition("\n")[2], location=Ref(scope="none"),
        owner=Action(verb="none"), agent_next="다음 감사에서 재확인",
        recovery="not_applicable", detail=Result(outcome="executed"),
    )
