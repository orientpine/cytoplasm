from __future__ import annotations

import json
import os
from pathlib import Path

from automation.memory_curator.state import AlertState, CuratorState, PromotionRecord
from automation.memory_curator.state_store import load_state, save_state


def _populated_state() -> CuratorState:
    record = PromotionRecord(
        source_kind="memory",
        entry_sha256="a" * 64,
        slug="note-a",
        created_at="2026-07-30T10:00:00Z",
        note_sha256="",
        draft_id="draft-a",
        confirm_message_id=None,
        status="prepared",
        posted_at=None,
        reconciled_at=None,
        backup_path=None,
        last_block_reason=None,
    )
    return CuratorState(
        3,
        {"prepared": record},
        AlertState("observed", None, None, "pending"),
        {},
    )


def test_save_state_when_path_is_new_is_atomic_private_and_roundtrips(tmp_path: Path) -> None:
    # Given: a state path whose parent does not exist.
    parent = tmp_path / "private"
    path = parent / "state.json"
    state = _populated_state()

    # When: the state is saved.
    save_state(path, state)

    # Then: parent/file modes are private, replacement leaves no temp, and data round-trips.
    assert oct(parent.stat().st_mode & 0o777) == "0o700"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert list(parent.glob(f".{path.name}.*")) == []
    assert load_state(path) == state


def test_save_state_when_existing_mode_is_0664_replaces_it_as_0600(tmp_path: Path) -> None:
    # Given: the live-style pre-existing state file with group/world-readable mode.
    path = tmp_path / "state.json"
    _ = path.write_text(json.dumps({"proposed": ["f" * 64]}), encoding="utf-8")
    os.chmod(path, 0o664)

    # When: migrated state is saved in v3 form.
    save_state(path, load_state(path))

    # Then: the replacement is owner-only and still contains the migrated record.
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert len(load_state(path).promotions) == 1
    assert list(tmp_path.glob(f".{path.name}.*")) == []
