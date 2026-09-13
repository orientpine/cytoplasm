"""관측 구간 없는 큐레이터 점검 결과의 선택적 통지 봉투."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage, Ref


class ReminderContent(str):
    """기존 문자열 콜백을 유지하면서 검색 전용 참조와 중복 없는 사실을 전달한다."""

    fact: str
    references: tuple[Ref, ...]

    def __new__(cls, content: str, fact: str, references: tuple[Ref, ...]) -> ReminderContent:
        value = super().__new__(cls, content)
        value.fact = fact
        value.references = references
        return value


def build_notice(content: str) -> OwnerMessage | None:
    """문자열 콜백의 점검 결과를 담되 구세대 런타임에서는 원문으로 보낸다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    location = Ref(scope="none")
    fact = content
    if isinstance(content, ReminderContent):
        fact = content.fact
        location = content.references[0]
        if len(content.references) > 1:
            location = Ref(scope="resource", url=None, search=("Discord 검색", ", ".join(
                ref.search[1] for ref in content.references if ref.search is not None)))
    return OwnerMessage(
        subject_key="memory-curator", subject="메모리 큐레이터 점검", fact=fact,
        location=location, owner=Action(verb="none"),
        agent_next="다음 주기에 메모리 상태를 다시 확인합니다.",
        recovery="not_applicable", detail=Result(outcome="executed"),
    )
