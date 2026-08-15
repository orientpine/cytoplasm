"""Characterization tests for the calendar/coordination owner-approval stores.

These lock the CURRENT observable behavior of the append-only JSONL pending
stores so an upcoming "exactly one live approval message per logical key"
refactor (which adds one optional ``key`` field) cannot silently change the
serialization, the round-trip, the legacy-record tolerance, or the draft-id
lookup contract. Behavior asserted here is characterized, not endorsed.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "calendar" / "scripts"))
sys.path.insert(0, str(_REPO / "skills" / "coordination" / "scripts"))

import calendar_confirm  # noqa: E402
import calendar_gate  # noqa: E402
import calendar_pending  # noqa: E402
import coordination_lifecycle  # noqa: E402
import coordination_pending  # noqa: E402
from coordinate_io import CoordinationError  # noqa: E402

CREATED = datetime(2026, 7, 17, 9, 30, tzinfo=UTC)

CALENDAR_LINE = (
    '{"created":"2026-07-17T09:30:00Z","dm_channel_id":"dm-1",'
    '"dm_message_id":"msg-1","draft_id":"abc123",'
    '"key":"calendar:__orphan__:abc123","sha256":"sha-123"}'
)
COORDINATION_LINE = (
    '{"correlation":"coord-123","created":"2026-07-17T09:30:00Z",'
    '"dm_channel_id":"dm-1","dm_message_id":"msg-1","draft_id":"abc123",'
    '"duration_min":30,"key":"coord:2026-07-18T09:00:00+09:00",'
    '"sha256":"sha-123","slot":"2026-07-18T09:00:00+09:00",'
    '"summary":"피어 미팅"}'
)


def _calendar_entry(*, draft_id: str = "abc123", dm_message_id: str = "msg-1"):
    return calendar_pending.PendingConfirm(
        draft_id=draft_id, sha256="sha-123", dm_channel_id="dm-1",
        dm_message_id=dm_message_id, created=CREATED,
    )


def _coordination_entry(*, draft_id: str = "abc123", dm_message_id: str = "msg-1"):
    return coordination_pending.PendingConfirm(
        draft_id=draft_id, sha256="sha-123", dm_channel_id="dm-1",
        dm_message_id=dm_message_id, slot="2026-07-18T09:00:00+09:00",
        summary="피어 미팅", correlation="coord-123", duration_min=30, created=CREATED,
    )


def _calendar_store(tmp_path: Path, monkeypatch) -> calendar_pending.PendingConfirmStore:
    path = tmp_path / "calendar-pending-confirms.jsonl"
    monkeypatch.setenv("CALENDAR_PENDING_CONFIRMS", str(path))
    return calendar_pending.PendingConfirmStore(path)


def _coordination_store(tmp_path: Path, monkeypatch) -> coordination_pending.PendingConfirmStore:
    path = tmp_path / "coordination-pending-confirms.jsonl"
    monkeypatch.setenv("COORDINATION_PENDING_CONFIRMS", str(path))
    return coordination_pending.PendingConfirmStore(path)


# --- calendar: serialization -------------------------------------------------


def test_calendar_pending_line_is_byte_exact(tmp_path: Path, monkeypatch) -> None:
    # Given a fully populated calendar pending confirmation
    store = _calendar_store(tmp_path, monkeypatch)

    # When it is appended to the JSONL store
    store.append(_calendar_entry())

    # Then the façade transfer adds key only to new bytes; legacy tolerance below stays unchanged.
    assert store.path.read_text(encoding="utf-8") == CALENDAR_LINE + "\n"


def test_calendar_pending_entry_round_trips_every_field(tmp_path: Path, monkeypatch) -> None:
    # Given an appended calendar pending confirmation
    store = _calendar_store(tmp_path, monkeypatch)
    entry = _calendar_entry()
    store.append(entry)

    # When the store is read back
    loaded = store.load()

    # Then every field survives, including the DM binding pair
    assert loaded == (entry,)
    assert (loaded[0].draft_id, loaded[0].sha256) == ("abc123", "sha-123")
    assert (loaded[0].dm_channel_id, loaded[0].dm_message_id) == ("dm-1", "msg-1")
    assert loaded[0].created == CREATED


def test_calendar_pending_parses_hand_written_legacy_record(
    tmp_path: Path, monkeypatch
) -> None:
    # Given a legacy line holding ONLY the fields the current writer emits,
    # in a deliberately unsorted key order
    store = _calendar_store(tmp_path, monkeypatch)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        '{"sha256":"sha-123","draft_id":"abc123","dm_message_id":"msg-1",'
        '"dm_channel_id":"dm-1","created":"2026-07-17T09:30:00Z"}\n',
        encoding="utf-8",
    )

    # When it is parsed
    loaded = store.load()

    # Then the absence of any later-added optional field does not raise
    assert loaded == (_calendar_entry(),)


def test_calendar_pending_parses_record_with_unknown_extra_field(
    tmp_path: Path, monkeypatch
) -> None:
    # Given a record carrying a field the current reader knows nothing about
    store = _calendar_store(tmp_path, monkeypatch)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        CALENDAR_LINE[:-1] + ',"unknown_future_field":"x"}\n', encoding="utf-8"
    )

    # When it is parsed
    loaded = store.load()

    # Then the unknown field is ignored rather than rejected
    assert loaded == (_calendar_entry(),)


def test_calendar_draft_record_has_exact_field_set(tmp_path: Path, monkeypatch) -> None:
    # Given a gate directory isolated from any real state
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))

    # When a draft is created
    record = calendar_gate.create_draft(
        action="delete", argv=("gws", "calendar", "events", "delete"),
        calendar_id="primary", event_id="evt1", summary="private",
        start="", end="", channel_id="dm",
    )

    # Then the persisted draft carries exactly these keys
    assert sorted(record.keys()) == [
        "action", "argv", "calendar_id", "channel_id", "created", "end",
        "event_id", "id", "sha256", "start", "status", "summary",
    ]


# --- calendar: lookup contract -----------------------------------------------


def test_calendar_lookup_returns_the_sole_entry_for_a_draft_id(
    tmp_path: Path, monkeypatch
) -> None:
    # Given two entries for two distinct draft ids
    store = _calendar_store(tmp_path, monkeypatch)
    store.append(_calendar_entry())
    store.append(_calendar_entry(draft_id="def456", dm_message_id="msg-2"))

    # When the lookup runs for one of them
    found = calendar_confirm._pending_entry("abc123")

    # Then only that draft's entry comes back
    assert found == _calendar_entry()


def test_calendar_lookup_on_missing_draft_id(tmp_path: Path, monkeypatch) -> None:
    # Given an empty store
    _calendar_store(tmp_path, monkeypatch)

    # When the lookup runs optionally, then mandatorily
    assert calendar_confirm._pending_entry("nope", required=False) is None
    with pytest.raises(calendar_gate.GateError) as raised:
        calendar_confirm._pending_entry("nope")

    # Then the mandatory lookup fails closed with the unconfirmed exit code
    assert raised.value.exit_code == 1


def test_calendar_lookup_rejects_duplicate_draft_ids(tmp_path: Path, monkeypatch) -> None:
    # Given the append-only store holding two live entries for ONE draft id
    # (characterized, not endorsed — the duplicate is what the producer writes today)
    store = _calendar_store(tmp_path, monkeypatch)
    store.append(_calendar_entry())
    store.append(_calendar_entry(dm_message_id="msg-2"))

    # When the lookup runs, optionally and mandatorily
    with pytest.raises(calendar_gate.GateError) as optional_raised:
        calendar_confirm._pending_entry("abc123", required=False)
    with pytest.raises(calendar_gate.GateError) as required_raised:
        calendar_confirm._pending_entry("abc123")

    # Then both fail closed — non-uniqueness is never resolved silently
    assert (optional_raised.value.exit_code, required_raised.value.exit_code) == (1, 1)


# --- coordination: serialization ---------------------------------------------


def test_coordination_pending_line_is_byte_exact(tmp_path: Path, monkeypatch) -> None:
    # Given a fully populated coordination pending confirmation
    store = _coordination_store(tmp_path, monkeypatch)

    # When it is appended to the JSONL store
    store.append(_coordination_entry())

    # Then the façade transfer adds key only to new bytes; legacy tolerance below stays unchanged.
    assert store.path.read_text(encoding="utf-8") == COORDINATION_LINE + "\n"


def test_coordination_pending_entry_round_trips_every_field(
    tmp_path: Path, monkeypatch
) -> None:
    # Given an appended coordination pending confirmation
    store = _coordination_store(tmp_path, monkeypatch)
    entry = _coordination_entry()
    store.append(entry)

    # When the store is read back
    loaded = store.load()

    # Then every field survives, including the DM binding pair
    assert loaded == (entry,)
    assert (loaded[0].dm_channel_id, loaded[0].dm_message_id) == ("dm-1", "msg-1")
    assert (loaded[0].slot, loaded[0].summary) == ("2026-07-18T09:00:00+09:00", "피어 미팅")
    assert (loaded[0].correlation, loaded[0].duration_min) == ("coord-123", 30)
    assert loaded[0].created == CREATED


def test_coordination_pending_parses_hand_written_legacy_record(
    tmp_path: Path, monkeypatch
) -> None:
    # Given a legacy line holding ONLY the fields the current writer emits,
    # in a deliberately unsorted key order
    store = _coordination_store(tmp_path, monkeypatch)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        '{"summary":"피어 미팅","slot":"2026-07-18T09:00:00+09:00","sha256":"sha-123",'
        '"duration_min":30,"draft_id":"abc123","dm_message_id":"msg-1",'
        '"dm_channel_id":"dm-1","created":"2026-07-17T09:30:00Z",'
        '"correlation":"coord-123"}\n',
        encoding="utf-8",
    )

    # When it is parsed
    loaded = store.load()

    # Then the absence of any later-added optional field does not raise
    assert loaded == (_coordination_entry(),)


def test_coordination_pending_parses_record_with_unknown_extra_field(
    tmp_path: Path, monkeypatch
) -> None:
    # Given a record carrying a field the current reader knows nothing about
    store = _coordination_store(tmp_path, monkeypatch)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        COORDINATION_LINE[:-1] + ',"unknown_future_field":"x"}\n', encoding="utf-8"
    )

    # When it is parsed
    loaded = store.load()

    # Then the unknown field is ignored rather than rejected
    assert loaded == (_coordination_entry(),)


# --- coordination: lookup contract -------------------------------------------


def test_coordination_lookup_returns_the_sole_entry_for_a_draft_id(
    tmp_path: Path, monkeypatch
) -> None:
    # Given two entries for two distinct draft ids
    store = _coordination_store(tmp_path, monkeypatch)
    store.append(_coordination_entry())
    store.append(_coordination_entry(draft_id="def456", dm_message_id="msg-2"))

    # When the lookup runs for one of them
    found = coordination_lifecycle._pending_entry("abc123")

    # Then only that draft's entry comes back
    assert found == _coordination_entry()


def test_coordination_lookup_on_missing_draft_id(tmp_path: Path, monkeypatch) -> None:
    # Given an empty store
    _coordination_store(tmp_path, monkeypatch)

    # When the lookup runs optionally, then mandatorily
    assert coordination_lifecycle._pending_entry("nope", required=False) is None
    with pytest.raises(CoordinationError) as raised:
        coordination_lifecycle._pending_entry("nope")

    # Then the mandatory lookup fails closed with the unconfirmed exit code
    assert raised.value.exit_code == 1


def test_coordination_lookup_rejects_duplicate_draft_ids(
    tmp_path: Path, monkeypatch
) -> None:
    # Given the append-only store holding two live entries for ONE draft id
    # (characterized, not endorsed — the duplicate is what the producer writes today)
    store = _coordination_store(tmp_path, monkeypatch)
    store.append(_coordination_entry())
    store.append(_coordination_entry(dm_message_id="msg-2"))

    # When the lookup runs, optionally and mandatorily
    with pytest.raises(CoordinationError) as optional_raised:
        coordination_lifecycle._pending_entry("abc123", required=False)
    with pytest.raises(CoordinationError) as required_raised:
        coordination_lifecycle._pending_entry("abc123")

    # Then both fail closed — non-uniqueness is never resolved silently
    assert (optional_raised.value.exit_code, required_raised.value.exit_code) == (1, 1)
