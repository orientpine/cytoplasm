"""Pure agent-to-agent scheduling coordination logic for W3-3.

State machine + slot intersection + candidate selection + deadlock rules.
No I/O, no clock, no network — the runtime driver executes the emitted
commands and feeds observed events back in. Invariants enforced here:

- ``ExecuteCalendarWrite`` is emitted only after BOTH the peer approval and
  the owner approval events have been observed (calendar write = gate).
- Every terminal failure phase (deadlock timeout, zero candidates, refusal)
  emits a human notification command and NEVER a write command.
- Refusal allows exactly ONE renegotiation round before terminating.
- Deadlock: production timeout is 10 minutes (``PRODUCTION_TIMEOUT_S``);
  tests inject a short timeout through the driver's ``--timeout-s``.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Final

PRODUCTION_TIMEOUT_S: Final = 600  # 10 min no-response deadlock rule (plan W3-3)
MAX_CANDIDATES: Final = 3
QUERY_AVAILABILITY: Final = "query_availability"
RESPONSE_AVAILABILITY: Final = "response_availability"
QUERY_CONFIRM_SLOT: Final = "query_confirm_slot"
RESPONSE_CONFIRM_SLOT: Final = "response_confirm_slot"
CORRELATION_PREFIX: Final = "coord-"
# Bot gateways MUST skip this human-facing confirmation notice (never dispatch
# it to an LLM turn) — §2.3 cascade-safety rule, enforced in the interop plugin.
TEAM_NOTICE_PREFIX: Final = "📅 일정 확정 (coord-"


class Phase(str, Enum):
    """Coordination lifecycle phases (terminal phases never emit writes)."""

    INIT = "init"
    AWAIT_AVAILABILITY = "await_availability"
    AWAIT_PEER_CONFIRM = "await_peer_confirm"
    AWAIT_OWNER_CONFIRM = "await_owner_confirm"
    EXECUTE = "execute"
    DONE = "done"
    DEADLOCK_TIMEOUT = "deadlock_timeout"
    DEADLOCK_NO_SLOTS = "deadlock_no_slots"
    REFUSED = "refused"


TERMINAL_FAILURES: Final = frozenset(
    {Phase.DEADLOCK_TIMEOUT, Phase.DEADLOCK_NO_SLOTS, Phase.REFUSED}
)


@dataclass(frozen=True, slots=True)
class Command:
    """One side effect the driver must perform, in emission order."""

    kind: str  # send_availability_query | send_slot_confirm | request_owner_confirm
    #           | execute_calendar_write | notify_escalation | notify_termination
    #           | post_team_confirmation | notify_result
    slot: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CoordinationState:
    """Immutable machine state; drivers persist nothing else."""

    phase: Phase
    candidates: tuple[str, ...] = ()
    cursor: int = 0
    renegotiated: bool = False

    @property
    def chosen_slot(self) -> str:
        if not self.candidates or self.cursor >= len(self.candidates):
            raise ValueError("no candidate slot selected")
        return self.candidates[self.cursor]


def start() -> tuple[CoordinationState, tuple[Command, ...]]:
    """Begin coordination by querying the peer's availability."""
    return (
        CoordinationState(phase=Phase.AWAIT_AVAILABILITY),
        (Command(kind="send_availability_query"),),
    )


def on_availability(
    state: CoordinationState, candidates: tuple[str, ...]
) -> tuple[CoordinationState, tuple[Command, ...]]:
    """Receive the intersected candidate list (already capped by the caller)."""
    _expect(state, Phase.AWAIT_AVAILABILITY)
    if not candidates:
        terminal = replace(state, phase=Phase.DEADLOCK_NO_SLOTS)
        return terminal, (Command(kind="notify_escalation", reason="no_candidates"),)
    advanced = replace(state, phase=Phase.AWAIT_PEER_CONFIRM, candidates=candidates, cursor=0)
    return advanced, (Command(kind="send_slot_confirm", slot=candidates[0]),)


def on_timeout(state: CoordinationState) -> tuple[CoordinationState, tuple[Command, ...]]:
    """No peer response within the deadlock window → escalate, zero writes."""
    _expect(state, Phase.AWAIT_AVAILABILITY, Phase.AWAIT_PEER_CONFIRM)
    terminal = replace(state, phase=Phase.DEADLOCK_TIMEOUT)
    return terminal, (Command(kind="notify_escalation", reason="peer_timeout"),)


def on_peer_confirm(
    state: CoordinationState, accepted: bool
) -> tuple[CoordinationState, tuple[Command, ...]]:
    """Peer approved or declined the currently proposed slot."""
    _expect(state, Phase.AWAIT_PEER_CONFIRM)
    if accepted:
        advanced = replace(state, phase=Phase.AWAIT_OWNER_CONFIRM)
        return advanced, (Command(kind="request_owner_confirm", slot=state.chosen_slot),)
    return _renegotiate_or_terminate(state, declined_by="peer")


def on_owner_confirm(
    state: CoordinationState, accepted: bool
) -> tuple[CoordinationState, tuple[Command, ...]]:
    """Owner (cha) approved or declined; write happens only on approval."""
    _expect(state, Phase.AWAIT_OWNER_CONFIRM)
    if accepted:
        advanced = replace(state, phase=Phase.EXECUTE)
        return advanced, (Command(kind="execute_calendar_write", slot=state.chosen_slot),)
    return _renegotiate_or_terminate(state, declined_by="owner")


def on_executed(state: CoordinationState) -> tuple[CoordinationState, tuple[Command, ...]]:
    """The gated calendar write succeeded → terse team notice + owner result."""
    _expect(state, Phase.EXECUTE)
    finished = replace(state, phase=Phase.DONE)
    return finished, (
        Command(kind="post_team_confirmation", slot=state.chosen_slot),
        Command(kind="notify_result", slot=state.chosen_slot),
    )


def _renegotiate_or_terminate(
    state: CoordinationState, *, declined_by: str
) -> tuple[CoordinationState, tuple[Command, ...]]:
    next_cursor = state.cursor + 1
    if state.renegotiated or next_cursor >= len(state.candidates):
        terminal = replace(state, phase=Phase.REFUSED)
        return terminal, (
            Command(kind="notify_termination", reason=f"declined_by_{declined_by}"),
        )
    renegotiating = replace(
        state, phase=Phase.AWAIT_PEER_CONFIRM, cursor=next_cursor, renegotiated=True
    )
    return renegotiating, (
        Command(kind="send_slot_confirm", slot=renegotiating.chosen_slot),
    )


def _expect(state: CoordinationState, *phases: Phase) -> None:
    if state.phase not in phases:
        raise ValueError(f"event not valid in phase {state.phase.value}")


def candidate_slots(
    *,
    peer_slots: tuple[str, ...],
    busy: tuple[tuple[datetime, datetime], ...],
    range_start: datetime,
    range_end: datetime,
    duration_min: int,
    limit: int = MAX_CANDIDATES,
) -> tuple[str, ...]:
    """Intersect the peer's offered slots with my free time, capped at ``limit``.

    A slot survives when it parses as an aware ISO datetime, the whole
    ``[slot, slot+duration)`` interval fits inside the requested range, and it
    overlaps none of my busy intervals. Result is sorted and de-duplicated.
    """
    duration = timedelta(minutes=duration_min)
    valid: list[datetime] = []
    for raw in peer_slots:
        slot = _parse_aware(raw)
        if slot is None:
            continue
        slot_end = slot + duration
        if slot < range_start or slot_end > range_end:
            continue
        if any(slot < busy_end and slot_end > busy_start for busy_start, busy_end in busy):
            continue
        valid.append(slot)
    unique = sorted(set(valid))
    return tuple(slot.isoformat() for slot in unique[:limit])


def parse_busy_intervals(items: tuple[dict, ...]) -> tuple[tuple[datetime, datetime], ...]:
    """Extract aware busy intervals from ``gws calendar events list`` items."""
    intervals: list[tuple[datetime, datetime]] = []
    for item in items:
        start_raw = item.get("start", {}).get("dateTime", "")
        end_raw = item.get("end", {}).get("dateTime", "")
        start = _parse_aware(str(start_raw))
        end = _parse_aware(str(end_raw))
        if start is not None and end is not None and end > start:
            intervals.append((start, end))
    return tuple(intervals)


def confirm_slot_accepted(payload: dict) -> bool:
    """Interpret a §2.3 ``response_confirm_slot`` payload; anything else declines."""
    return payload.get("result") == "accepted"


def availability_slots(payload: dict) -> tuple[str, ...]:
    """Extract offered slot strings from a §2.3 ``response_availability`` payload."""
    slots = payload.get("slots")
    if not isinstance(slots, list):
        return ()
    return tuple(str(slot) for slot in slots)


def team_notice(correlation_id: str, time_label: str) -> str:
    """Render the terse #team confirmation (no title, no directives, no mentions).

    Byte-parity with ``TEAM_NOTICE_PREFIX`` so every bot gateway skips it —
    the §2.3 cascade-safety rule.
    """
    notice = f"📅 일정 확정 ({correlation_id}): {time_label} — 양측 승인 완료."
    if not notice.startswith(TEAM_NOTICE_PREFIX):
        raise ValueError("correlation id must carry the coord- prefix")
    return notice


def _parse_aware(raw: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo is not None else None
