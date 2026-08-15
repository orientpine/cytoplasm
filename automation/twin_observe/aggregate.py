from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from automation.twin_observe.ledgers import GateEvent, Verdict

ActionKey: TypeAlias = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ActionTally:
    skill: str
    action: str
    approves: int
    rejects: int
    events: tuple[GateEvent, ...]


def _event_order(event: GateEvent) -> tuple[str, str, str, str, str]:
    return event.ts, event.ledger, event.skill, event.action, event.verdict.value


def aggregate_events(events: tuple[GateEvent, ...]) -> tuple[ActionTally, ...]:
    grouped: dict[ActionKey, list[GateEvent]] = {}
    for event in events:
        grouped.setdefault((event.skill, event.action), []).append(event)
    tallies: list[ActionTally] = []
    for (skill, action), group in sorted(grouped.items()):
        ordered_events = tuple(sorted(group, key=_event_order))
        approves = sum(event.verdict is Verdict.APPROVE for event in ordered_events)
        rejects = sum(event.verdict is Verdict.REJECT for event in ordered_events)
        tallies.append(ActionTally(skill, action, approves, rejects, ordered_events))
    return tuple(tallies)
