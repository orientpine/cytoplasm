"""원본 보관의 전사 경계와 비공개 원장을 검증한다."""
from __future__ import annotations

from pathlib import Path

import pytest

from automation.plaud_sync.transcribe import process
from tests.unit.test_plaud_sync_transcribe import FakeEffects, _RECORD, _fail, _ok


@pytest.mark.parametrize(("code", "outcome", "deleted"), [(0, "planned", True), (4, "retry", False)])
def test_characterize_transcription_cleanup(code: int, outcome: str, deleted: bool) -> None:
    effects = FakeEffects(results=[_ok() if code == 0 else _fail(code)])
    assert process(_RECORD, effects=effects, max_attempts=2) == outcome
    assert bool(effects.discarded) is deleted
    assert effects.commits[0][1].status == ("planned" if code == 0 else "transcribing")


def test_characterize_discard_without_optin(tmp_path: Path) -> None:
    from automation.plaud_sync.transcribe_live import LiveEffects

    audio = tmp_path / "sample.m4a"
    audio.write_bytes(b"original")
    effects = LiveEffects(tmp_path, tmp_path / "watch.lock", {"HOME": str(tmp_path)})
    effects.discard_audio(audio)
    assert not audio.exists()


@pytest.mark.parametrize("code", [0, 4])
def test_downloaded_audio_is_archived_even_on_transcription_failure(code: int) -> None:
    from automation.plaud_sync.audio import AudioSource

    archived: list[Path] = []

    class Observed(FakeEffects):
        def archive_audio(self, path: Path, source: AudioSource, stem: str) -> None:
            assert source.duration_ms > 0
            assert stem == Path(_RECORD.note_relpath).stem
            assert not self.discarded
            archived.append(path)

    effects = Observed(results=[_ok() if code == 0 else _fail(code)])
    assert process(_RECORD, effects=effects, max_attempts=2) == ("planned" if code == 0 else "retry")
    assert len(archived) == 1


def test_opted_in_discard_keeps_unarchived_audio(tmp_path: Path) -> None:
    from automation.plaud_sync.transcribe_live import LiveEffects

    audio = tmp_path / "sample.opus"
    audio.write_bytes(b"original")
    effects = LiveEffects(tmp_path, tmp_path / "watch.lock",
                          {"HOME": str(tmp_path), "DRIVE_PUBLISH_ENABLED": "1"})
    effects.discard_audio(audio)
    assert audio.exists()
