"""보관 실패·중단의 경계와 실제 tick의 원본 바이트 흐름을 검증한다."""
from __future__ import annotations

import io
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.plaud_sync import audio_manifest, transcribe_live, transcribe_live_effects
from automation.plaud_sync.audio import AudioSource, download
from automation.plaud_sync.fetch import CloudTranscript
from automation.plaud_sync.lifelog_model import ExtractionSkipped, LifelogRecording
from automation.plaud_sync.model import PlaudSyncState
from automation.plaud_sync.store import load_state, save_note_body, save_state
from automation.plaud_sync.transcribe import CliResult
from tests.unit.test_plaud_sync_archive_drive import (
    ArchiveSetup, archive, setup_archive as setup_archive,
)
from tests.unit.test_plaud_sync_transcribe import _DRAFT_BODY, _RECORD, _SOURCE, _TRANSCRIPT_MD


@pytest.mark.parametrize("suffix", [".m4a", ".opus", ".wav", ".mp3"])
def test_real_tick_preserves_download_container_bytes(setup_archive: ArchiveSetup,
                                                     monkeypatch: pytest.MonkeyPatch, suffix: str) -> None:
    fake, _, env, audio = setup_archive
    payload = audio.read_bytes()
    source = replace(_SOURCE, suffix=suffix)
    state_dir = audio.parent / "plaud-sync"
    save_state(state_dir / "state.json", PlaudSyncState(1, None, {_RECORD.recording_id: _RECORD}))
    save_note_body(state_dir, _RECORD.recording_id, _DRAFT_BODY)

    class Response(io.BytesIO):
        headers: dict[str, str] = {"Content-Length": str(len(payload))}

        def read(self, size: int | None = -1) -> bytes:
            return super().read(size)

    class Bound(transcribe_live.LiveEffects):
        def fetch_source(self, recording_id: str) -> AudioSource:
            return source

        def fetch_summary(self, recording_id: str) -> str:
            return "- synthetic summary"

        def fetch_transcript(self, recording_id: str) -> CloudTranscript:
            return CloudTranscript("")

        def transcribe(self, audio: Path, label: str) -> CliResult:
            assert audio.suffix == suffix
            assert audio.read_bytes() == payload
            transcript = state_dir.parent / "result.md"
            transcript.write_text(_TRANSCRIPT_MD, encoding="utf-8")
            return CliResult(0, transcript, "local:test", "")

        def extract(self, recording: LifelogRecording) -> ExtractionSkipped:
            return ExtractionSkipped("test")

    def original_download(source: AudioSource, dest: Path, *, max_bytes: int, opener: object) -> Path:
        return download(source, dest, max_bytes=max_bytes, opener=lambda url, timeout: Response(payload))

    monkeypatch.setattr(transcribe_live, "LiveEffects", Bound)
    monkeypatch.setattr(transcribe_live_effects, "download", original_download)
    env["TERM_GLOSSARY_CACHE"] = str(audio.parent / "terms")
    env["TERM_CORRECTION_LOG"] = str(audio.parent / "terms" / "corrections.jsonl")
    summary = transcribe_live.run_transcribe_step(
        state_dir=state_dir, lock_path=state_dir / "watch.lock", env=env,
        now=lambda: datetime(2026, 9, 7, tzinfo=UTC),
    )
    assert summary is not None and summary.promoted == 1
    assert load_state(state_dir / "state.json").records[_RECORD.recording_id].status == "planned"
    assert not (state_dir / "audio" / f"{source.recording_id}{suffix}").exists()
    assert tuple(fake.file_bytes.values()) == (payload,)
    assert len(audio_manifest.rows(audio_manifest.manifest_path(env))) == 1


def test_repeated_interrupts_at_manifest_replace_leave_one_copy(setup_archive: ArchiveSetup,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _, env, audio = setup_archive
    original = Path.replace

    def interrupted(path: Path, target: Path) -> Path:
        if target.name == "manifest.jsonl":
            raise KeyboardInterrupt
        return original(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", interrupted)
        for _ in range(2):
            with pytest.raises(KeyboardInterrupt):
                archive(setup_archive)
            assert not audio_manifest.manifest_path(env).exists()
            assert not list(audio_manifest.manifest_path(env).parent.glob(".manifest-*"))
            assert audio.exists()
    assert archive(setup_archive)
    assert fake.uploads == len(fake.files) == 1
    assert len(audio_manifest.rows(audio_manifest.manifest_path(env))) == 1


def test_download_changed_after_archive_is_not_deleted(setup_archive: ArchiveSetup) -> None:
    _, _, env, audio = setup_archive
    assert archive(setup_archive)
    audio.write_bytes(b"different download")
    transcribe_live.LiveEffects(audio.parent, audio.parent / "watch.lock", env).discard_audio(audio)
    assert audio.exists()


def test_manifest_write_failure_keeps_audio_and_retry_reuses_copy(setup_archive: ArchiveSetup,
                                                               monkeypatch: pytest.MonkeyPatch,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    fake, _, env, audio = setup_archive
    original = Path.replace

    def denied(path: Path, target: Path) -> Path:
        if target.name == "manifest.jsonl":
            raise OSError("synthetic disk failure")
        return original(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", denied)
        assert not archive(setup_archive)
    transcribe_live.LiveEffects(audio.parent, audio.parent / "watch.lock", env).discard_audio(audio)
    assert audio.exists()
    assert capsys.readouterr().err.count("AUDIO-ARCHIVE-FAIL") == 1
    assert archive(setup_archive)
    assert fake.uploads == len(fake.files) == 1
