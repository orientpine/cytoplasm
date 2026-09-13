"""그림자 탐지 통지 봉투 — 이름 대조·틱 상태는 워처가 소유한다."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage


def shadow_message(content: str, names: tuple[str, ...], home: Path) -> OwnerMessage | None:
    """관측 구간 없는 단일 탐지 결과에 실제 자가 스킬 루트 검색 위치를 붙인다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    return OwnerMessage(
        subject_key=" ".join(names), subject="자가 스킬 이름 가림", fact=content,
        location=Ref(scope="resource", search=("자가 스킬 루트", str(home / ".hermes" / "skills"))),
        owner=Action(verb="none"), agent_next="다음 틱에서 이름 대조",
        recovery="not_applicable", detail=Result(outcome="executed"),
    )
