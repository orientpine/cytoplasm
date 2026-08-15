from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

import pytest

from automation.memory_curator import state as state_module
from automation.memory_curator.model import MemoryKind
from automation.memory_curator.state import (
    AlertState,
    CuratorState,
    PromotionRecord,
    PromotionStatus,
    StateError,
    empty_state,
    parse_state,
    serialize_state,
)
from automation.memory_curator.state_store import load_state


def _promotion(
    status: PromotionStatus,
    *,
    source_kind: MemoryKind = "memory",
    suffix: str = "a",
) -> PromotionRecord:
    return PromotionRecord(
        source_kind=source_kind,
        entry_sha256=suffix * 64,
        slug=f"note-{suffix}",
        created_at="2026-07-30T10:00:00Z",
        note_sha256="" if status == "prepared" else "f" * 64,
        draft_id=f"draft-{suffix}",
        confirm_message_id=None if status == "prepared" else f"message-{suffix}",
        status=status,
        posted_at=None if status == "prepared" else "2026-07-30T10:01:00Z",
        reconciled_at="2026-07-30T10:02:00Z" if status == "reconciled" else None,
        backup_path="/private/backup" if status == "reconciled" else None,
        last_block_reason=None,
    )


def _raw_promotion(status: str = "prepared") -> dict[str, object]:
    return {
        "source_kind": "memory",
        "entry_sha256": "a" * 64,
        "slug": "note-a",
        "created_at": "2026-07-30T10:00:00Z",
        "note_sha256": "",
        "draft_id": "draft-a",
        "confirm_message_id": None,
        "status": status,
        "posted_at": None,
        "reconciled_at": None,
        "backup_path": None,
        "last_block_reason": None,
    }


def _raw_v2(record: dict[str, object]) -> dict[str, object]:
    return {
        "version": 2,
        "promotions": {"promotion-a": record},
        "alert": {
            "last_observed_signature": None,
            "last_sent_signature": None,
            "last_sent_at": None,
            "pending_signature": None,
        },
    }


def test_empty_state_when_created_is_v3_with_empty_alert_and_outbox() -> None:
    # Given: no persisted curator state.
    # When: an empty state is created.
    state = empty_state()

    # Then: it uses the exact v3 defaults.
    assert state == CuratorState(
        version=3,
        promotions={},
        alert=AlertState(None, None, None, None),
        pending_owner_events={},
    )


def test_parse_state_when_v1_has_eight_hashes_preserves_all_as_audit_only() -> None:
    # Given: the live v1 shape containing eight old content hashes.
    hashes = [f"{index:064x}" for index in range(1, 9)]

    # When: the v1 document is parsed.
    state = parse_state({"proposed": hashes})

    # Then: every hash is retained as a legacy, non-delete-eligible audit record.
    assert state.version == 3
    assert state.pending_owner_events == {}
    assert len(state.promotions) == 8
    assert set(state.promotions) == set(hashes)
    for old_hash in hashes:
        assert state.promotions[old_hash] == PromotionRecord(
            source_kind="memory",
            entry_sha256=old_hash,
            slug="",
            created_at="",
            note_sha256="",
            draft_id=None,
            confirm_message_id=None,
            status="legacy_unbound",
            posted_at=None,
            reconciled_at=None,
            backup_path=None,
            last_block_reason=None,
        )


def test_parse_state_when_v2_migrates_with_an_empty_outbox() -> None:
    # Given: an exact persisted v2 document.
    raw = _raw_v2(_raw_promotion("posted"))

    # When: it crosses the versioned state boundary.
    state = parse_state(raw)

    # Then: it migrates to v3 without inventing owner events.
    assert state.version == 3
    assert state.pending_owner_events == {}


def test_parse_serialize_roundtrip_when_v3_has_mixed_statuses_and_events() -> None:
    # Given: a v3 state spanning promotion statuses and both owner-event phases.
    legacy = PromotionRecord(
        source_kind="memory",
        entry_sha256="d" * 64,
        slug="",
        created_at="",
        note_sha256="",
        draft_id=None,
        confirm_message_id=None,
        status="legacy_unbound",
        posted_at=None,
        reconciled_at=None,
        backup_path=None,
        last_block_reason="audit-only",
    )
    state = CuratorState(
        version=3,
        promotions={
            "prepared": _promotion("prepared", suffix="a"),
            "posted": _promotion("posted", source_kind="user", suffix="b"),
            "reconciled": _promotion("reconciled", suffix="c"),
            "legacy": legacy,
        },
        alert=AlertState("observed", "sent", "2026-07-30T11:00:00Z", "pending"),
        pending_owner_events={
            "promotion-b#posted": state_module.PendingOwnerEvent(
                "promotion-b#posted",
                "posted",
                "masked posted preview",
                "principle",
                "draft-b",
                None,
            ),
            "promotion-c#deleted": state_module.PendingOwnerEvent(
                "promotion-c#deleted",
                "deleted",
                "masked deleted preview",
                None,
                None,
                37,
            ),
        },
    )

    # When: it is serialized and parsed at the trust boundary.
    parsed = parse_state(serialize_state(state))

    # Then: no typed state is lost or coerced.
    assert parsed == state


def test_curator_state_when_built_copies_mappings_into_immutable_snapshots() -> None:
    # Given: mutable caller-owned promotion and outbox mappings.
    promotions = {"prepared": _promotion("prepared", suffix="a")}
    pending = {
        "prepared#posted": state_module.PendingOwnerEvent(
            "prepared#posted", "posted", "preview", "principle", "draft-a", None
        )
    }

    # When: a curator state is constructed and both inputs are later changed.
    state = CuratorState(3, promotions, AlertState(None, None, None, None), pending)
    promotions["posted"] = _promotion("posted", suffix="b")
    pending.clear()

    # Then: both snapshots are isolated and expose immutable mappings.
    assert set(state.promotions) == {"prepared"}
    assert isinstance(state.promotions, MappingProxyType)
    assert set(state.pending_owner_events) == {"prepared#posted"}
    assert isinstance(state.pending_owner_events, MappingProxyType)


@pytest.mark.parametrize(
    "event",
    [
        {
            "phase": "unknown",
            "preview": "preview",
            "twin_kind": None,
            "draft_id": None,
            "freed_chars": 3,
        },
        {
            "phase": "deleted",
            "preview": "preview",
            "twin_kind": None,
            "draft_id": None,
        },
    ],
)
def test_parse_state_when_pending_event_is_invalid_fails_closed(
    event: dict[str, object],
) -> None:
    # Given: a v3 document with an invalid pending owner event.
    raw = _raw_v2(_raw_promotion())
    raw["version"] = 3
    raw["pending_owner_events"] = {"promotion-a#deleted": event}

    # When / Then: parsing rejects rather than guessing the event semantics.
    with pytest.raises(StateError):
        _ = parse_state(raw)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"proposed": "not-a-list"},
        {"proposed": [], "extra": True},
        {
            "version": 2.0,
            "promotions": {},
            "alert": {
                "last_observed_signature": None,
                "last_sent_signature": None,
                "last_sent_at": None,
                "pending_signature": None,
            },
        },
        {"version": 2, "promotions": {}, "alert": {}, "extra": True},
    ],
)
def test_parse_state_when_top_level_shape_is_unknown_fails_closed(raw: object) -> None:
    # Given: a document that is neither exact v1 nor exact v2.
    # When / Then: parsing rejects it with the public typed error.
    with pytest.raises(StateError):
        _ = parse_state(raw)


def test_parse_state_when_promotion_field_is_missing_fails_closed() -> None:
    # Given: a v2 promotion without one required field.
    record = _raw_promotion()
    del record["note_sha256"]

    # When / Then: parsing rejects the incomplete record.
    with pytest.raises(StateError):
        _ = parse_state(_raw_v2(record))


def test_parse_state_when_promotion_status_is_unknown_fails_closed() -> None:
    # Given: a structurally complete v2 promotion with an unknown status.
    # When / Then: parsing rejects rather than coercing the status.
    with pytest.raises(StateError):
        _ = parse_state(_raw_v2(_raw_promotion("unknown")))


def test_load_state_when_file_is_missing_returns_empty_state(tmp_path: Path) -> None:
    # Given: a path with no state file.
    # When: it is loaded.
    state = load_state(tmp_path / "state.json")

    # Then: loading returns the v3 empty state.
    assert state == empty_state()


def test_load_state_when_file_is_v1_returns_migrated_v3(tmp_path: Path) -> None:
    # Given: a real v1 JSON file.
    old_hash = "e" * 64
    path = tmp_path / "state.json"
    _ = path.write_text(json.dumps({"proposed": [old_hash]}), encoding="utf-8")

    # When: it is loaded through the disk boundary.
    state = load_state(path)

    # Then: the v1 hash is migrated to the audit-only v3 representation.
    assert state.version == 3
    assert state.promotions[old_hash].status == "legacy_unbound"
    assert state.pending_owner_events == {}
