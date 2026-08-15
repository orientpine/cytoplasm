from __future__ import annotations

import pytest

from automation.memory_relocate.model import (
    RelocationError,
    RelocationRecord,
    RelocationState,
    parse_state,
    record_key,
    serialize_state,
)


def _record() -> RelocationRecord:
    return RelocationRecord(
        version=1,
        source_kind="memory",
        entry_sha256="a" * 64,
        note_relpath="Reference/note-a.md",
        note_plan_sha256="b" * 64,
        reclaimable_chars=321,
        action_hash=f"sha256:{'c' * 64}",
        status="reconciled",
        kind="memory_relocation",
        surface="owner_dm",
        channel_id="channel-1",
        policy_version=1,
        message_id="message-1",
        created_at="2026-07-31T10:00:00Z",
        approved_at="2026-07-31T10:01:00Z",
        written_at="2026-07-31T10:02:00Z",
        reconciled_at="2026-07-31T10:03:00Z",
        remote_ref="obsidian:Reference/note-a.md",
        note_content_sha256="d" * 64,
        rag_source_key="obsidian:Reference/note-a.md",
        rag_fingerprint="e" * 64,
        backup_path="/private/backup-a",
        last_block_reason=None,
    )


def _raw_record() -> dict[str, object]:
    return {
        "version": 1,
        "source_kind": "memory",
        "entry_sha256": "a" * 64,
        "note_relpath": "Reference/note-a.md",
        "note_plan_sha256": "b" * 64,
        "reclaimable_chars": 321,
        "action_hash": f"sha256:{'c' * 64}",
        "status": "proposed",
        "kind": "memory_relocation",
        "surface": "owner_dm",
        "channel_id": "channel-1",
        "policy_version": 1,
        "message_id": None,
        "created_at": "2026-07-31T10:00:00Z",
        "approved_at": None,
        "written_at": None,
        "reconciled_at": None,
        "remote_ref": None,
        "note_content_sha256": None,
        "rag_source_key": None,
        "rag_fingerprint": None,
        "backup_path": None,
        "last_block_reason": None,
    }


def _raw_state(record: dict[str, object]) -> dict[str, object]:
    return {"version": 1, "relocations": {f"memory:{'a' * 64}": record}}


def test_parse_serialize_roundtrip_when_v1_record_is_complete() -> None:
    # Given: a complete typed v1 relocation state.
    record = _record()
    key = record_key(record.source_kind, record.entry_sha256)
    state = RelocationState(version=1, relocations={key: record})

    # When: it is serialized and parsed at the persisted-state boundary.
    parsed = parse_state(serialize_state(state))

    # Then: every typed field survives without coercion.
    assert parsed == state


def test_serialize_state_when_record_is_keyed_uses_record_key() -> None:
    # Given: a relocation indexed by its source-qualified digest.
    raw = _raw_state(_raw_record())
    state = parse_state(raw)

    # When: the state is serialized.
    serialized = serialize_state(state)

    # Then: the persisted map uses the exact public record-key format.
    assert record_key("memory", "digest") == "memory:digest"
    assert serialized == raw


@pytest.mark.parametrize(
    "record",
    [
        {key: value for key, value in _raw_record().items() if key != "version"},
        {**_raw_record(), "extra": True},
    ],
)
def test_parse_state_when_record_shape_is_not_exact_fails_closed(
    record: dict[str, object],
) -> None:
    # Given: a relocation record with a missing or extra field.
    # When / Then: parsing rejects rather than filling or ignoring fields.
    with pytest.raises(RelocationError):
        _ = parse_state(_raw_state(record))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "unknown"),
        ("source_kind", "system"),
        ("reclaimable_chars", True),
        ("policy_version", 1.0),
        ("message_id", False),
    ],
)
def test_parse_state_when_record_field_is_invalid_fails_closed(
    field: str,
    value: object,
) -> None:
    # Given: an exact-shape record containing one invalid typed value.
    record = _raw_record()
    record[field] = value

    # When / Then: parsing rejects without coercing the persisted value.
    with pytest.raises(RelocationError):
        _ = parse_state(_raw_state(record))


@pytest.mark.parametrize(
    "raw",
    [
        {"version": 1, "relocations": {}, "extra": True},
        {"version": 2, "relocations": {}},
    ],
)
def test_parse_state_when_root_contract_is_invalid_fails_closed(
    raw: dict[str, object],
) -> None:
    # Given: a persisted document with an unknown shape or version.
    # When / Then: the v1 parser rejects it through the public error type.
    with pytest.raises(RelocationError):
        _ = parse_state(raw)


def test_parse_state_when_map_key_does_not_bind_record_fails_closed() -> None:
    # Given: a complete record stored under a different source-qualified digest.
    raw = _raw_state(_raw_record())
    raw["relocations"] = {"memory:different": _raw_record()}

    # When / Then: parsing rejects the mismatched deletion-target key.
    with pytest.raises(RelocationError):
        _ = parse_state(raw)
