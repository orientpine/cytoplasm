from __future__ import annotations

from dataclasses import replace

import pytest

from automation.plaud_sync.model import (
    PlaudSyncError,
    PlaudSyncRecord,
    PlaudSyncState,
    empty_state,
    parse_state,
    serialize_state,
)


_BASE = PlaudSyncRecord(
    version=1,
    recording_id="rec-001",
    recorded_at="2026-09-01T08:00:00Z",
    note_relpath="000_PARA/Area/Lifelog/2026/2026-09-01-standup--abcdef123456.md",
    note_title="standup (2026-09-01)",
    body_sha256="a" * 64,
    action_hash=f"sha256:{'b' * 64}",
    status="planned",
    kind="obsidian-write",
    surface="agent-chat-thread",
    channel_id="",
    policy_version=8,
    message_id=None,
    created_at="2026-09-01T09:00:00Z",
    approved_at=None,
    written_at=None,
    remote_ref=None,
    note_content_sha256=None,
    last_block_reason=None,
)


def _record(**overrides: object) -> PlaudSyncRecord:
    return replace(_BASE, **overrides)


def test_state_roundtrip_preserves_records_and_watermark() -> None:
    record = _record(status="posted", message_id="m1", channel_id="c1", approval_thread_id="t1")
    state = PlaudSyncState(version=1, last_poll_at="2026-09-01T10:00:00Z", records={"rec-001": record})
    parsed = parse_state(serialize_state(state))
    assert parsed.last_poll_at == "2026-09-01T10:00:00Z"
    assert parsed.records["rec-001"] == record


def test_record_without_approval_thread_id_still_parses() -> None:
    state = PlaudSyncState(version=1, last_poll_at=None, records={"rec-001": _record()})
    raw = serialize_state(state)
    records = raw["records"]
    assert isinstance(records, dict)
    row = records["rec-001"]
    assert isinstance(row, dict)
    row.pop("approval_thread_id", None)
    parsed = parse_state(raw)
    assert parsed.records["rec-001"].approval_thread_id is None


def test_unknown_record_key_is_fail_closed() -> None:
    state = PlaudSyncState(version=1, last_poll_at=None, records={"rec-001": _record()})
    raw = serialize_state(state)
    records = raw["records"]
    assert isinstance(records, dict)
    row = records["rec-001"]
    assert isinstance(row, dict)
    row["surprise"] = "x"
    with pytest.raises(PlaudSyncError):
        _ = parse_state(raw)


def test_invalid_status_is_rejected() -> None:
    state = PlaudSyncState(version=1, last_poll_at=None, records={"rec-001": _record()})
    raw = serialize_state(state)
    records = raw["records"]
    assert isinstance(records, dict)
    row = records["rec-001"]
    assert isinstance(row, dict)
    row["status"] = "shipped"
    with pytest.raises(PlaudSyncError):
        _ = parse_state(raw)


def test_empty_state_has_no_records_and_no_watermark() -> None:
    state = empty_state()
    assert state.records == {}
    assert state.last_poll_at is None
    assert state.version == 1


def test_records_mapping_is_immutable() -> None:
    state = PlaudSyncState(version=1, last_poll_at=None, records={"rec-001": _record()})
    with pytest.raises(TypeError):
        state.records["rec-002"] = _record(recording_id="rec-002")  # type: ignore[index]
