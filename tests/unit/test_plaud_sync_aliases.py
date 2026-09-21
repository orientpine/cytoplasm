"""Identity aliases preserve existing approval bindings after folder moves."""
from __future__ import annotations

import importlib
import importlib.util
from dataclasses import replace
from itertools import combinations

import pytest

from automation.plaud_sync import model
from automation.plaud_sync.model import PlaudStatus, PlaudSyncState, parse_record, serialize_record
from automation.plaud_sync.sync import plan_new_records
from automation.plaud_sync.watch_step import resolve_tick
from tests.unit.test_plaud_sync_model import _BASE as RECORD
from tests.unit.test_plaud_sync_sync import _BASE, _NO_EXTRACTION, _NOW
from tests.unit.test_plaud_sync_watch_step import Effects

TAIL = "0123456789abcdef0123456789abcdef"
PREFIXED = f"of_{TAIL}"
ORDER: tuple[PlaudStatus, ...] = ("written", "approved", "posted", "planned", "transcribing", "abandoned")


@pytest.mark.parametrize("raw,expected", [(TAIL, TAIL), (PREFIXED, TAIL), ("rec_of_x", "rec_of_x")])
def test_canonical_identity_when_folder_prefix_changes(raw: str, expected: str) -> None:
    # Given
    canonical = getattr(model, "canonical_recording_id", None)
    assert callable(canonical), "canonical_recording_id must be provided"
    # When / Then
    assert canonical(raw) == expected


@pytest.mark.parametrize("known,incoming", [(TAIL, PREFIXED), (PREFIXED, TAIL)])
def test_discovery_creates_no_record_or_card_when_alias_is_known(known: str, incoming: str) -> None:
    # Given
    record = replace(RECORD, recording_id=known, status="written")
    state = PlaudSyncState(1, None, {known: record})
    effects = Effects()
    # When
    result = plan_new_records(state, (replace(_BASE, id=incoming),), now=_NOW,
                              policy_version=8, extractor=_NO_EXTRACTION)
    resolved = resolve_tick(result.state, effects=effects.effects())
    # Then
    assert result.state.records == state.records
    assert result.planned == () and result.bodies == {}
    assert resolved.posted == () and effects.posted == []


def test_discovery_deduplicates_when_both_ids_arrive_in_one_poll() -> None:
    # Given
    recordings = tuple(replace(_BASE, id=key) for key in (TAIL, PREFIXED))
    # When
    result = plan_new_records(PlaudSyncState(1, None, {}), recordings, now=_NOW,
                              policy_version=8, extractor=_NO_EXTRACTION)
    # Then
    assert result.planned == (TAIL,)


@pytest.mark.parametrize("higher,lower", list(combinations(ORDER, 2)))
@pytest.mark.parametrize("reverse", [False, True])
def test_migration_keeps_advanced_binding_when_aliases_collide(
    higher: PlaudStatus, lower: PlaudStatus, reverse: bool,
) -> None:
    # Given
    assert importlib.util.find_spec("automation.plaud_sync.aliases") is not None, "migration module required"
    migrate = importlib.import_module("automation.plaud_sync.aliases").migrate_aliases
    winner = replace(RECORD, recording_id=TAIL, status=higher, message_id="winner-message")
    loser = replace(RECORD, recording_id=PREFIXED, status=lower, action_hash="loser-hash")
    rows = (loser, winner) if reverse else (winner, loser)
    state = PlaudSyncState(1, "2026-09-01T00:00:00Z", {r.recording_id: r for r in rows})
    # When
    merged, report = migrate(state)
    # Then
    assert tuple(merged.records) == (TAIL,)
    assert merged.records[TAIL] == replace(winner, aliases=(PREFIXED,))
    assert merged.last_poll_at == state.last_poll_at and report
    assert len(state.records) == 2
    assert migrate(merged) == (merged, ())


def test_migration_preserves_existing_aliases_when_rank_ties() -> None:
    # Given
    assert importlib.util.find_spec("automation.plaud_sync.aliases") is not None, "migration module required"
    migrate = importlib.import_module("automation.plaud_sync.aliases").migrate_aliases
    first = parse_record({**serialize_record(RECORD), "recording_id": TAIL, "aliases": ["old-id"]})
    second = replace(RECORD, recording_id=PREFIXED)
    state = PlaudSyncState(1, None, {PREFIXED: second, TAIL: first})
    # When
    merged, _ = migrate(state)
    # Then: tie uses lexicographic key, not input order; unrelated metadata stays intact.
    assert merged.records[TAIL] == replace(first, aliases=("old-id", PREFIXED))


def test_discovery_matches_stored_alias_when_primary_id_differs() -> None:
    # Given
    record = parse_record({**serialize_record(RECORD), "aliases": [PREFIXED]})
    state = PlaudSyncState(1, None, {record.recording_id: record})
    # When
    result = plan_new_records(state, (replace(_BASE, id=TAIL),), now=_NOW,
                              policy_version=8, extractor=_NO_EXTRACTION)
    # Then
    assert result.state.records == state.records and result.planned == ()


def test_legacy_record_loads_when_optional_diagnostics_are_absent() -> None:
    # Given
    row = serialize_record(RECORD)
    row.pop("aliases", None)
    row.pop("last_recheck_error", None)
    # When
    parsed = parse_record(row)
    # Then
    assert getattr(parsed, "aliases", None) == ()
    assert hasattr(parsed, "last_recheck_error") and parsed.last_recheck_error is None


@pytest.mark.parametrize("aliases", [None, "old-id", [7], [True], {}])
def test_alias_parser_refuses_malformed_state_when_aliases_are_not_strings(aliases: object) -> None:
    # Given
    row = {**serialize_record(RECORD), "aliases": aliases}
    # When / Then
    with pytest.raises(model.PlaudSyncError):
        parse_record(row)


def test_optional_fields_roundtrip_when_present() -> None:
    # Given
    row = {**serialize_record(RECORD), "aliases": [PREFIXED], "last_recheck_error": "get_note: PlaudMcpError"}
    # When
    parsed = parse_record(row)
    # Then
    assert serialize_record(parsed) == row
