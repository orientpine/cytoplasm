from __future__ import annotations

import json
import os
from pathlib import Path

from automation.memory_relocate.model import (
    RelocationRecord,
    RelocationState,
    empty_state,
    record_key,
    serialize_state,
)
from automation.memory_relocate.store import load_state, save_state


def _state() -> RelocationState:
    record = RelocationRecord(
        version=1,
        source_kind="memory",
        entry_sha256="f" * 64,
        note_relpath="Reference/note-f.md",
        note_plan_sha256="e" * 64,
        reclaimable_chars=144,
        action_hash=f"sha256:{'d' * 64}",
        status="proposed",
        kind="memory_relocation",
        surface="owner_dm",
        channel_id="channel-1",
        policy_version=1,
        message_id=None,
        created_at="2026-07-31T10:00:00Z",
        approved_at=None,
        written_at=None,
        reconciled_at=None,
        remote_ref=None,
        note_content_sha256=None,
        rag_source_key=None,
        rag_fingerprint=None,
        backup_path=None,
        last_block_reason=None,
    )
    return RelocationState(
        version=1,
        relocations={record_key(record.source_kind, record.entry_sha256): record},
    )


def test_load_state_when_file_is_missing_returns_empty_state(tmp_path: Path) -> None:
    # Given: no relocations.json file exists.
    # When: the missing path is loaded.
    state = load_state(tmp_path / "relocations.json")

    # Then: loading returns the exact empty v1 state.
    assert state == empty_state()


def test_load_state_when_file_contains_v1_document_parses_it(tmp_path: Path) -> None:
    # Given: a real UTF-8 relocations.json containing a complete v1 document.
    expected = _state()
    path = tmp_path / "relocations.json"
    _ = path.write_text(json.dumps(serialize_state(expected)), encoding="utf-8")

    # When: the file crosses the disk boundary.
    loaded = load_state(path)

    # Then: it is parsed into the original typed state.
    assert loaded == expected


def test_save_state_when_parent_is_new_is_atomic_private_and_roundtrips(
    tmp_path: Path,
) -> None:
    # Given: a state path whose private parent does not exist.
    parent = tmp_path / "private"
    path = parent / "relocations.json"
    state = _state()

    # When: the state is saved.
    save_state(path, state)

    # Then: modes are private, no temp remains, and the replacement parses.
    assert oct(parent.stat().st_mode & 0o777) == "0o700"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert list(parent.glob(f".{path.name}.*")) == []
    assert load_state(path) == state


def test_save_state_when_target_is_permissive_replaces_it_as_0600(
    tmp_path: Path,
) -> None:
    # Given: a pre-existing world-readable target.
    path = tmp_path / "relocations.json"
    _ = path.write_text("{}", encoding="utf-8")
    os.chmod(path, 0o666)

    # When: typed state atomically replaces it.
    save_state(path, _state())

    # Then: the replacement is owner-only and leaves no temporary file.
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert list(tmp_path.glob(f".{path.name}.*")) == []
