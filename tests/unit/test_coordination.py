"""W3-3 coordination protocol: pure state machine + intersection + peer brain."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from automation.interop import coordination
from automation.interop.coordination import (
    Command,
    CoordinationState,
    Phase,
    availability_slots,
    candidate_slots,
    confirm_slot_accepted,
    on_availability,
    on_executed,
    on_owner_confirm,
    on_peer_confirm,
    on_timeout,
    parse_busy_intervals,
    start,
)
from automation.interop.delegation import InteropEnvelope, response_for

KST = timezone(timedelta(hours=9), "KST")
BASE = datetime(2026, 7, 17, 9, 0, tzinfo=KST)


def _kinds(commands: tuple[Command, ...]) -> list[str]:
    return [command.kind for command in commands]


def _slots(count: int, *, step_hours: int = 1) -> tuple[str, ...]:
    return tuple((BASE + timedelta(hours=index * step_hours)).isoformat() for index in range(count))


def _happy_until_peer_confirm() -> CoordinationState:
    state, _ = start()
    state, _ = on_availability(state, _slots(3))
    return state


def test_production_timeout_is_ten_minutes() -> None:
    assert coordination.PRODUCTION_TIMEOUT_S == 600


def test_happy_path_writes_only_after_both_approvals() -> None:
    # Given / When
    state, commands = start()
    assert _kinds(commands) == ["send_availability_query"]
    state, commands = on_availability(state, _slots(3))
    assert _kinds(commands) == ["send_slot_confirm"]
    assert commands[0].slot == _slots(3)[0]
    state, commands = on_peer_confirm(state, True)
    assert _kinds(commands) == ["request_owner_confirm"]
    state, commands = on_owner_confirm(state, True)

    # Then — the ONLY write command, after peer AND owner both accepted
    assert _kinds(commands) == ["execute_calendar_write"]
    state, commands = on_executed(state)
    assert _kinds(commands) == ["post_team_confirmation", "notify_result"]
    assert state.phase is Phase.DONE


def test_timeout_during_availability_escalates_without_write() -> None:
    # Given
    state, _ = start()

    # When
    state, commands = on_timeout(state)

    # Then
    assert state.phase is Phase.DEADLOCK_TIMEOUT
    assert _kinds(commands) == ["notify_escalation"]
    assert commands[0].reason == "peer_timeout"


def test_timeout_during_peer_confirm_escalates_without_write() -> None:
    # Given
    state = _happy_until_peer_confirm()

    # When
    state, commands = on_timeout(state)

    # Then
    assert state.phase is Phase.DEADLOCK_TIMEOUT
    assert _kinds(commands) == ["notify_escalation"]


def test_zero_candidates_is_a_deadlock_with_escalation() -> None:
    # Given
    state, _ = start()

    # When
    state, commands = on_availability(state, ())

    # Then
    assert state.phase is Phase.DEADLOCK_NO_SLOTS
    assert _kinds(commands) == ["notify_escalation"]
    assert commands[0].reason == "no_candidates"


def test_peer_decline_renegotiates_exactly_once_then_terminates() -> None:
    # Given
    state = _happy_until_peer_confirm()

    # When — first decline renegotiates with the NEXT candidate
    state, commands = on_peer_confirm(state, False)

    # Then
    assert state.phase is Phase.AWAIT_PEER_CONFIRM
    assert state.renegotiated
    assert _kinds(commands) == ["send_slot_confirm"]
    assert commands[0].slot == _slots(3)[1]

    # When — second decline terminates with zero writes
    state, commands = on_peer_confirm(state, False)

    # Then
    assert state.phase is Phase.REFUSED
    assert _kinds(commands) == ["notify_termination"]
    assert commands[0].reason == "declined_by_peer"


def test_owner_decline_shares_the_single_renegotiation_budget() -> None:
    # Given
    state = _happy_until_peer_confirm()
    state, _ = on_peer_confirm(state, True)

    # When — owner declines: renegotiate (peer must re-approve next slot)
    state, commands = on_owner_confirm(state, False)
    assert state.phase is Phase.AWAIT_PEER_CONFIRM
    assert _kinds(commands) == ["send_slot_confirm"]
    state, _ = on_peer_confirm(state, True)
    state, commands = on_owner_confirm(state, False)

    # Then — second decline (budget spent) terminates
    assert state.phase is Phase.REFUSED
    assert commands[0].reason == "declined_by_owner"


def test_decline_with_single_candidate_terminates_immediately() -> None:
    # Given
    state, _ = start()
    state, _ = on_availability(state, _slots(1))

    # When
    state, commands = on_peer_confirm(state, False)

    # Then
    assert state.phase is Phase.REFUSED
    assert _kinds(commands) == ["notify_termination"]


def test_no_terminal_failure_path_ever_emits_a_write_command() -> None:
    # Given three failure runs
    runs: list[tuple[Command, ...]] = []
    state, commands = start()
    _, escalated = on_timeout(state)
    runs.append(commands + escalated)
    state, commands = start()
    _, empty = on_availability(state, ())
    runs.append(commands + empty)
    state = _happy_until_peer_confirm()
    _, first = on_peer_confirm(state, False)

    # Then
    for emitted in runs:
        assert "execute_calendar_write" not in _kinds(emitted)
    assert "execute_calendar_write" not in _kinds(first)


def test_events_outside_their_phase_are_rejected() -> None:
    state, _ = start()
    with pytest.raises(ValueError):
        on_peer_confirm(state, True)
    with pytest.raises(ValueError):
        on_executed(state)
    terminal, _ = on_timeout(state)
    with pytest.raises(ValueError):
        on_timeout(terminal)


def test_candidate_slots_caps_at_three_sorted_unique() -> None:
    # Given eight peer slots offered out of order with a duplicate
    peer = _slots(8)[::-1] + (_slots(1)[0],)

    # When
    result = candidate_slots(
        peer_slots=peer, busy=(), range_start=BASE,
        range_end=BASE + timedelta(hours=9), duration_min=30,
    )

    # Then
    assert result == _slots(3)


def test_candidate_slots_excludes_busy_overlap_and_range_violations() -> None:
    # Given: busy 10:00-11:00; range ends 12:00 with 45-min duration
    busy = ((BASE + timedelta(hours=1), BASE + timedelta(hours=2)),)

    # When
    result = candidate_slots(
        peer_slots=_slots(4), busy=busy, range_start=BASE,
        range_end=BASE + timedelta(hours=3), duration_min=45,
    )

    # Then: 09:00 fits, 10:00/11:15-overlap excluded... 11:00 ends 11:45 ok
    assert result == (BASE.isoformat(), (BASE + timedelta(hours=2)).isoformat())


def test_candidate_slots_drops_naive_and_malformed_entries() -> None:
    result = candidate_slots(
        peer_slots=("bogus", "2026-07-17T12:00:00", "interop-ready"), busy=(),
        range_start=BASE, range_end=BASE + timedelta(hours=9), duration_min=30,
    )
    assert result == ()


def test_parse_busy_intervals_reads_gws_items_and_skips_all_day() -> None:
    items = (
        {"start": {"dateTime": BASE.isoformat()}, "end": {"dateTime": (BASE + timedelta(hours=1)).isoformat()}},
        {"start": {"date": "2026-07-17"}, "end": {"date": "2026-07-18"}},
        {"start": {"dateTime": "broken"}, "end": {"dateTime": BASE.isoformat()}},
    )
    assert parse_busy_intervals(items) == ((BASE, BASE + timedelta(hours=1)),)


def test_response_payload_helpers() -> None:
    assert confirm_slot_accepted({"result": "accepted"})
    assert not confirm_slot_accepted({"result": "declined"})
    assert not confirm_slot_accepted({})
    assert availability_slots({"slots": ["a", "b"]}) == ("a", "b")
    assert availability_slots({"slots": "oops"}) == ()


def _query(intent: str, payload: dict) -> InteropEnvelope:
    return InteropEnvelope("coord-test", "agent-cha", "peer-test", intent, payload)


def test_response_for_offers_deterministic_hour_aligned_slots() -> None:
    # Given a §2.3 range payload 09:30→13:00 KST, 30 min
    query = _query("query_availability", {
        "range_start": (BASE + timedelta(minutes=30)).isoformat(),
        "range_end": (BASE + timedelta(hours=4)).isoformat(),
        "duration_min": 30,
    })

    # When — twice, to prove determinism
    first = response_for(query, sender_id="peer-test")
    second = response_for(query, sender_id="peer-test")

    # Then: hour-aligned 10:00/11:00/12:00 KST + fits before 13:00 with 12:30 end
    expected = [(BASE + timedelta(hours=hour)).isoformat() for hour in (1, 2, 3)]
    assert first.payload == {"slots": expected}
    assert first == second
    assert first.intent == "response_availability"


def test_response_for_without_range_keeps_legacy_gate_marker() -> None:
    response = response_for(_query("query_availability", {"duration_min": 1}), sender_id="peer-test")
    assert response.payload == {"slots": ["interop-ready"]}


def test_response_for_confirm_slot_declines_by_default_and_echoes_slot() -> None:
    response = response_for(
        _query("query_confirm_slot", {"slot": BASE.isoformat(), "duration_min": 30}),
        sender_id="peer-test",
    )
    assert response.intent == "response_confirm_slot"
    assert response.payload == {"result": "declined", "slot": BASE.isoformat()}


def test_response_for_confirm_slot_declines_on_simulate_marker() -> None:
    response = response_for(
        _query("query_confirm_slot", {"slot": BASE.isoformat(), "simulate": "decline"}),
        sender_id="peer-test",
    )
    assert response.payload["result"] == "declined"


def test_response_for_unknown_query_rejects_with_explicit_reason() -> None:
    response = response_for(_query("query_unknown", {}), sender_id="peer-test")

    assert response.payload == {"result": "declined", "reason": "unsupported_intent"}


def test_response_for_slot_offer_caps_at_eight() -> None:
    query = _query("query_availability", {
        "range_start": BASE.isoformat(),
        "range_end": (BASE + timedelta(hours=24)).isoformat(),
        "duration_min": 30,
    })
    slots = response_for(query, sender_id="peer-test").payload["slots"]
    assert isinstance(slots, list)
    assert len(slots) == 8


def test_team_notice_is_terse_and_matches_the_bot_skip_prefix() -> None:
    # Given / When
    notice = coordination.team_notice("coord-abc123", "2026-07-17 (금) 09:00~09:30 KST")

    # Then — byte-parity with the plugin's cascade-safety skip rule
    assert notice.startswith(coordination.TEAM_NOTICE_PREFIX)
    assert "@" not in notice
    assert "?" not in notice
    with pytest.raises(ValueError):
        coordination.team_notice("w1-5-abc", "label")
