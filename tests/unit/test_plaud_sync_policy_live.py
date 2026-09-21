"""Policy integration through the runnable step with real state files and locks."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from automation import pipeline_lock
from automation.plaud_sync import transcribe_live
from automation.plaud_sync.audio import AudioSource
from automation.plaud_sync.fetch import CloudTranscript
from automation.plaud_sync.lifelog_model import ExtractionOutcome, ExtractionSkipped, LifelogRecording
from automation.plaud_sync.model import PlaudSyncState
from automation.plaud_sync.store import load_state, save_note_body, save_state
from automation.plaud_sync.transcribe import CliResult
from automation.term_correction import Correction, Glossary
from collections.abc import Sequence
from tests.unit.test_plaud_sync_transcribe import _RECORD, _SOURCE, _fail
from tests.unit.test_plaud_sync_transcribe_policy import EMPTY_DRAFT, NOW


class OfflineEffects(transcribe_live.LiveEffects):
    """Only external providers are replaced; persistence and locking are production code."""
    def fetch_source(self, recording_id: str) -> AudioSource:
        return replace(_SOURCE, recording_id=recording_id)

    def fetch_summary(self, recording_id: str) -> str:
        return ""

    def fetch_transcript(self, recording_id: str) -> CloudTranscript:
        return CloudTranscript("")

    def download(self, source: AudioSource) -> Path:
        return self.state_dir / "test-audio.mp3"

    def transcribe(self, audio: Path, label: str) -> CliResult:
        return _fail(5)

    def extract(self, recording: LifelogRecording) -> ExtractionOutcome:
        return ExtractionSkipped("test")

    def glossary(self) -> Glossary:
        return ()

    def record_corrections(self, recording: LifelogRecording, corrections: Sequence[Correction]) -> None:
        return None


def test_cloud_outage_keeps_record_when_give_up_is_reached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the real live summary adapter must distinguish an outage from an empty reply.
    class Unreachable:
        def __enter__(self) -> Unreachable:
            raise OSError("offline")
        def __exit__(self, *_exception: None) -> bool:
            return False
    class CloudOffline(OfflineEffects):
        fetch_summary = transcribe_live.LiveEffects.fetch_summary
    record = replace(_RECORD, transcribe_attempts=5)
    save_state(tmp_path / "state.json", PlaudSyncState(1, None, {record.recording_id: record}))
    monkeypatch.setattr(transcribe_live, "LiveEffects", CloudOffline)
    monkeypatch.setattr(transcribe_live, "PlaudMcpClient", Unreachable)
    # When
    transcribe_live.run_transcribe_step(state_dir=tmp_path, lock_path=tmp_path / "watch.lock",
                                      env={"HOME": str(tmp_path)}, now=lambda: NOW)
    # Then
    after = load_state(tmp_path / "state.json").records[record.recording_id]
    assert after.status == "transcribing"
    assert after.transcribe_attempts == 5
    assert after.last_block_reason == record.last_block_reason
    assert after.last_recheck_error == "get_note: OSError"


def test_commit_refuses_stale_retry_state_when_another_tick_changed_the_schedule(tmp_path: Path) -> None:
    # Given: same body/action hash, but a newer policy transition was persisted.
    current = replace(_RECORD, transcribe_attempts=2, next_transcribe_at=NOW.isoformat())
    save_state(tmp_path / "state.json", PlaudSyncState(1, None, {current.recording_id: current}))
    effects = OfflineEffects(tmp_path, tmp_path / "watch.lock", {})
    # When
    committed = effects.commit(_RECORD, replace(_RECORD, last_block_reason="old tick"), None)
    # Then
    assert committed is False
    assert load_state(tmp_path / "state.json").records[current.recording_id] == current


def test_step_journals_waiting_when_persisted_backoff_is_in_future(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    record = replace(_RECORD, transcribe_attempts=2, next_transcribe_at=(NOW + timedelta(hours=1)).isoformat())
    save_state(tmp_path / "state.json", PlaudSyncState(1, None, {record.recording_id: record}))
    monkeypatch.setattr(transcribe_live, "LiveEffects", OfflineEffects)
    # When
    result = transcribe_live.run_transcribe_step(state_dir=tmp_path, lock_path=tmp_path / "watch.lock", env={"HOME": str(tmp_path)}, now=lambda: NOW)
    # Then
    assert result is not None and result.line is not None
    assert "recording_id=rec-001" in result.line
    assert "outcome=waiting" in result.line
    assert f"next_transcribe_at={record.next_transcribe_at}" in result.line
    assert result.promoted == 0


def test_step_reads_give_up_override_when_attempt_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    record = replace(_RECORD, transcribe_attempts=2)
    save_state(tmp_path / "state.json", PlaudSyncState(1, None, {record.recording_id: record}))
    save_note_body(tmp_path, record.recording_id, EMPTY_DRAFT)
    monkeypatch.setattr(transcribe_live, "LiveEffects", OfflineEffects)
    # When
    result = transcribe_live.run_transcribe_step(state_dir=tmp_path, lock_path=tmp_path / "watch.lock", env={"HOME": str(tmp_path), "PLAUD_SYNC_TRANSCRIBE_GIVE_UP": "3"}, now=lambda: NOW)
    # Then
    assert result is not None and result.line is not None
    assert "recording_id=rec-001 outcome=abandoned" in result.line
    assert load_state(tmp_path / "state.json").records[record.recording_id].status == "abandoned"


def test_cloud_is_rechecked_when_pipeline_is_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    calls: list[str] = []
    class Observed(OfflineEffects):
        def fetch_summary(self, recording_id: str) -> str:
            calls.append(recording_id)
            return ""
    record = replace(_RECORD, transcribe_attempts=2, next_transcribe_at=(NOW + timedelta(hours=1)).isoformat())
    save_state(tmp_path / "state.json", PlaudSyncState(1, None, {record.recording_id: record}))
    monkeypatch.setattr(transcribe_live, "LiveEffects", Observed)
    env = {"HOME": str(tmp_path)}
    # When
    with pipeline_lock.hold(env) as acquired:
        assert acquired
        transcribe_live.run_transcribe_step(state_dir=tmp_path, lock_path=tmp_path / "watch.lock", env=env, now=lambda: NOW)
    # Then
    assert calls == [record.recording_id]
