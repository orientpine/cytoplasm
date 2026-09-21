"""Real watcher CLI migration in an isolated home, without cloud credentials."""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from automation.plaud_sync.model import PlaudSyncState
from automation.plaud_sync.store import load_state, save_state
from tests.unit.test_plaud_sync_aliases import PREFIXED, TAIL
from tests.unit.test_plaud_sync_model import _BASE

ROOT = Path(__file__).resolve().parents[2]
WATCHER = ROOT / "automation/plaud_sync/cron/plaud_sync_watch.py"


def test_cli_prints_plan_without_writing_when_migration_is_dry_run(tmp_path: Path) -> None:
    # Given
    path = tmp_path / ".hermes/plaud-sync/state.json"
    records = (replace(_BASE, recording_id=TAIL, status="written"), replace(_BASE, recording_id=PREFIXED))
    save_state(path, PlaudSyncState(1, None, {r.recording_id: r for r in records}))
    before = path.read_bytes()
    # When
    result = subprocess.run([sys.executable, str(WATCHER), "--migrate-aliases"],
                            env={"PATH": os.defpath, "HOME": str(tmp_path), "AUTOPHAGY_REPO_ROOT": str(ROOT)},
                            capture_output=True, text=True, timeout=30)
    # Then
    assert result.returncode == 0, result.stderr
    assert TAIL in result.stdout and PREFIXED in result.stdout
    assert path.read_bytes() == before
    assert list(path.parent.glob("state.json.bak-*")) == []


def test_cli_backs_up_original_state_when_migration_is_applied(tmp_path: Path) -> None:
    # Given
    path = tmp_path / ".hermes/plaud-sync/state.json"
    records = (replace(_BASE, recording_id=TAIL, status="written"), replace(_BASE, recording_id=PREFIXED))
    save_state(path, PlaudSyncState(1, None, {r.recording_id: r for r in records}))
    before = path.read_bytes()
    # When
    result = subprocess.run([sys.executable, str(WATCHER), "--migrate-aliases", "--apply"],
                            env={"PATH": os.defpath, "HOME": str(tmp_path), "AUTOPHAGY_REPO_ROOT": str(ROOT)},
                            capture_output=True, text=True, timeout=30)
    # Then
    assert result.returncode == 0, result.stderr
    backups = list(path.parent.glob("state.json.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert backups[0].stat().st_mode & 0o777 == 0o600
    state = load_state(path)
    assert tuple(state.records) == (TAIL,)
    assert state.records[TAIL].aliases == (PREFIXED,)


@pytest.mark.parametrize("args", [("--apply",), ("--migrate-aliases", "--repost-posted")])
def test_cli_refuses_ambiguous_mode_when_flags_are_mixed(tmp_path: Path, args: tuple[str, ...]) -> None:
    # Given / When
    result = subprocess.run([sys.executable, str(WATCHER), *args],
                            env={"PATH": os.defpath, "HOME": str(tmp_path), "AUTOPHAGY_REPO_ROOT": str(ROOT)},
                            capture_output=True, text=True, timeout=30)
    # Then
    assert result.returncode == 1
    assert not (tmp_path / ".hermes/plaud-sync/state.json").exists()
