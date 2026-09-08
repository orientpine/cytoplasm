"""Minimum-duration policy at discovery and the real transcription entry point."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from automation.plaud_sync import transcribe_live
from automation.plaud_sync.audio import AudioSource
from automation.plaud_sync.model import PlaudSyncState
from automation.plaud_sync.store import load_state, save_state, save_note_body
from automation.plaud_sync.transcribe import process
from tests.unit.test_plaud_sync_policy_live import OfflineEffects
from tests.unit.test_plaud_sync_transcribe import _RECORD, _SOURCE, _ok
from tests.unit.test_plaud_sync_transcribe_policy import CountingEffects, EMPTY_DRAFT, NOW
from tests.unit.test_plaud_sync_sync import _BASE
from tests.unit.test_plaud_sync_watch_transcribe import _load_watch


@pytest.mark.parametrize("scheduled", [False, True])
def test_short_source_is_abandoned_when_existing_record_is_processed(scheduled: bool) -> None:
    # Given
    effects = CountingEffects()
    effects.source = replace(_SOURCE, duration_ms=1000)
    effects.results = [_ok()]
    record = replace(_RECORD, next_transcribe_at=(NOW + timedelta(hours=1)).isoformat() if scheduled else None)
    # When
    outcome = process(record, effects=effects, max_attempts=2, now=lambda: NOW)
    # Then
    assert outcome == "abandoned"
    assert effects.labels == []
    assert effects.commits[0][1].status == "abandoned"
    assert effects.commits[0][1].transcribe_attempts == 0
    assert effects.commits[0][1].last_block_reason


@pytest.mark.parametrize("duration", [0, 4999, 5000])
def test_local_duration_boundary_when_audio_source_is_available(duration: int) -> None:
    # Given
    effects = CountingEffects()
    effects.source = replace(_SOURCE, duration_ms=duration)
    effects.results = [_ok()]
    # When
    outcome = process(_RECORD, effects=effects, max_attempts=2, now=lambda: NOW)
    # Then
    assert outcome == ("planned" if duration == 5000 else "abandoned")


def test_live_minimum_override_when_processing_existing_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    class ShortEffects(OfflineEffects):
        def fetch_source(self, recording_id: str) -> AudioSource:
            return replace(_SOURCE, duration_ms=6000)
    monkeypatch.setattr(transcribe_live, "LiveEffects", ShortEffects)
    save_state(tmp_path / "state.json", PlaudSyncState(1, None, {_RECORD.recording_id: _RECORD}))
    save_note_body(tmp_path, _RECORD.recording_id, EMPTY_DRAFT)
    # When
    transcribe_live.run_transcribe_step(state_dir=tmp_path, lock_path=tmp_path / "watch.lock",
        env={"HOME": str(tmp_path), "PLAUD_SYNC_MIN_DURATION_MS": "7000"}, now=lambda: NOW)
    # Then
    after = load_state(tmp_path / "state.json").records[_RECORD.recording_id]
    assert after.status == "abandoned"
    assert after.transcribe_attempts == 0


def test_discovery_journals_duration_when_no_note_or_card_is_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # Given
    from automation.plaud_sync import fetch, mcp_client
    watch = _load_watch(monkeypatch, tmp_path)
    monkeypatch.setenv("PLAUD_SYNC_MIN_DURATION_MS", "7000")
    recordings = (replace(_BASE, duration_ms=1000), replace(_BASE, id="rec-override", duration_ms=6000))
    class Client:
        def __enter__(self) -> Client:
            return self
        def __exit__(self, *_exception: None) -> bool:
            return False
    monkeypatch.setattr(mcp_client, "PlaudMcpClient", Client)
    monkeypatch.setattr(fetch, "fetch_recordings", lambda client, *, date_from: recordings)
    # When
    result = watch._discover(PlaudSyncState(1, None, {}), NOW)
    # Then
    assert result.records == {}
    assert not (tmp_path / "plaud-sync" / "notes").exists()
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    assert "recording_id=rec-001" in lines[0] and "duration_ms=1000" in lines[0]
    assert "recording_id=rec-override" in lines[1] and "duration_ms=6000" in lines[1]
