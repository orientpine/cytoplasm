"""적용 완료 통지 봉투. 판정·포인터 확인·멱등 마커는 호출자가 소유한다."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage


def applied_message(version: str, head: str, content: str) -> OwnerMessage | None:
    """실제 태그·커밋만 검색 키로 사용한다. 관측 구간 없는 일회성 결과다.

    v2 로 렌더한다 — 사실 인용이 줄바꿈을 지켜 남은 소유자 조치가 한 줄에 하나씩 보인다.
    """
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    return OwnerMessage(
        subject_key=head, subject=f"릴리스 {version}", fact=content,
        location=Ref(scope="resource", search=("릴리스 검색", f"{version} {head}")),
        owner=Action(verb="none"), agent_next="추가 실행 없음",
        recovery="not_applicable", detail=Result(outcome="executed"),
        render_version="owner-ko-v2",
    )
