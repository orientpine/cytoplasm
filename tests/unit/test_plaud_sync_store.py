from __future__ import annotations

import hashlib
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from automation.plaud_sync.model import PlaudSyncError, PlaudSyncRecord, PlaudSyncState
from automation.plaud_sync.store import (
    PlaudSyncStore,
    PlaudSyncStoreError,
    load_note_body,
    load_state,
    save_note_body,
    save_state,
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


def _state(*records: PlaudSyncRecord) -> PlaudSyncState:
    return PlaudSyncState(
        version=1,
        last_poll_at=None,
        records={record.recording_id: record for record in records},
    )


def test_save_then_load_roundtrip_with_0600(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(path, _state(_record()))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_state(path).records["rec-001"] == _record()


def test_load_missing_state_is_empty(tmp_path: Path) -> None:
    assert load_state(tmp_path / "absent.json").records == {}


def test_load_corrupt_state_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    _ = path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PlaudSyncError):
        _ = load_state(path)


def test_set_message_id_binds_once(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    record = _record()
    save_state(path, _state(record))
    store = PlaudSyncStore(path)
    store.set_message_id(record, "m1", "c1")
    bound = load_state(path).records["rec-001"]
    assert (bound.message_id, bound.channel_id) == ("m1", "c1")
    store.set_message_id(record, "m1", "c1")  # identical rebind is a no-op
    with pytest.raises(PlaudSyncStoreError):
        store.set_message_id(record, "m2", "c1")


def test_set_message_id_rejects_stale_action_hash(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(path, _state(_record()))
    stale = _record(action_hash=f"sha256:{'f' * 64}")
    with pytest.raises(PlaudSyncStoreError):
        PlaudSyncStore(path).set_message_id(stale, "m1", "c1")


def test_clear_message_id_is_compare_and_swap(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    record = _record(message_id="m1", channel_id="c1", status="posted")
    save_state(path, _state(record))
    store = PlaudSyncStore(path)
    store.clear_message_id("rec-001", record.action_hash, "other-message")
    assert load_state(path).records["rec-001"].message_id == "m1"
    store.clear_message_id("rec-001", record.action_hash, "m1")
    assert load_state(path).records["rec-001"].message_id is None


def test_update_rejects_binding_change(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    record = _record(message_id="m1", channel_id="c1", status="posted")
    save_state(path, _state(record))
    store = PlaudSyncStore(path)
    with pytest.raises(PlaudSyncStoreError):
        store.update(replace(record, message_id="m2"))
    store.update(replace(record, status="approved", approved_at="2026-09-01T10:00:00Z"))
    assert load_state(path).records["rec-001"].status == "approved"


def test_pending_excludes_terminal_statuses(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(
        path,
        _state(
            _record(),
            _record(recording_id="rec-002", status="written"),
            _record(recording_id="rec-003", status="abandoned"),
        ),
    )
    pending = PlaudSyncStore(path).all_pending()
    assert tuple(record.recording_id for record in pending) == ("rec-001",)


def test_note_body_roundtrip_with_0600(tmp_path: Path) -> None:
    body = "## 요약\n\n- x\n\n## 전문\n\n말씀\n"
    save_note_body(tmp_path, "rec-001", body)
    stored = tmp_path / "notes" / "rec-001.md"
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    loaded = load_note_body(tmp_path, "rec-001")
    assert loaded == body
    assert hashlib.sha256(loaded.encode("utf-8")).hexdigest() == hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_load_note_body_missing_is_none(tmp_path: Path) -> None:
    assert load_note_body(tmp_path, "rec-404") is None


def test_note_body_rejects_hostile_recording_id(tmp_path: Path) -> None:
    with pytest.raises(PlaudSyncStoreError):
        save_note_body(tmp_path, "../escape", "x")
