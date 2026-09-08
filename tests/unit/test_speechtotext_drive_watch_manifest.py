"""회의 워처의 마킹·정리 계약과 공용 manifest 연결을 검증한다."""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.plaud_sync import audio_manifest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "speechtotext" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import speechtotext_drive_watch as watcher  # noqa: E402
import stt_drive  # noqa: E402

NOW = datetime(2026, 9, 7, tzinfo=UTC)
PAYLOAD = b"synthetic meeting audio"


class FakeDrive:
    """기존 원본을 읽는 기능만 제공하여 업로드 시 테스트가 실패하게 한다."""
    def __init__(self) -> None:
        self.downloads: list[Path] = []

    def ensure_folder_path(self, parts: tuple[str, ...]) -> str:
        assert parts == ("autophagy", "회의녹음")
        return "watched-folder"

    def list_children(self, folder_id: str) -> list[dict[str, str]]:
        assert folder_id == "watched-folder"
        return [{"id": "synthetic-drive-file", "name": "sample.m4a"}]

    def verify_owner_only(self, file_id: str) -> None:
        assert file_id == "synthetic-drive-file"

    def download_file(self, file_id: str, dest: Path) -> str:
        assert file_id == "synthetic-drive-file"
        _ = dest.write_bytes(PAYLOAD)
        self.downloads.append(dest)
        return hashlib.sha256(PAYLOAD).hexdigest()


def environment(root: Path) -> dict[str, str]:
    return {"HOME": str(root), "SPEECHTOTEXT_DRIVE_FOLDER": "autophagy/회의녹음",
            "AUTOPHAGY_RUNTIME_ROOT": str(SCRIPTS.parents[2])}


@pytest.mark.parametrize("code", [0, 6])
def test_characterize_watch_marking_and_cleanup(tmp_path: Path, code: int) -> None:
    env = environment(tmp_path)
    drive = FakeDrive()
    calls: list[list[str]] = []

    def runner(argv: list[str], child: dict[str, str]) -> int:
        assert child["HOME"] == str(tmp_path)
        assert Path(argv[argv.index("--file") + 1]).read_bytes() == PAYLOAD
        calls.append(argv)
        return code

    summary = watcher.run_once(client=drive, env=env, runner=runner, now=NOW)
    assert summary == {"scanned": 1, "ingested": int(code == 0), "failed": int(code != 0), "skipped": 0}
    assert bool(stt_drive.load_state(stt_drive.state_path(env))) is (code == 0)
    assert len(calls) == 1
    assert not drive.downloads[0].parent.exists()


@pytest.mark.parametrize("code", [0, 6])
def test_ingest_records_existing_audio_through_shared_manifest(tmp_path: Path, code: int,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    env = environment(tmp_path)
    drive = FakeDrive()
    original = audio_manifest.record_manifest
    calls: list[audio_manifest.ManifestEntry] = []

    def record(entry: audio_manifest.ManifestEntry, environment: dict[str, str]) -> bool:
        calls.append(entry)
        return original(entry, environment)

    monkeypatch.setattr(audio_manifest, "record_manifest", record)
    summary = watcher.run_once(client=drive, env=env, runner=lambda argv, child: code, now=NOW)
    assert summary["ingested"] == int(code == 0)
    assert len(calls) == 1
    row, = audio_manifest.rows(audio_manifest.manifest_path(env))
    assert row == {"recording_id": hashlib.sha256(PAYLOAD).hexdigest()[:8], "domain": "meeting",
                   "audio_sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                   "drive_file_id": "synthetic-drive-file", "duration_ms": 0,
                   "transcript_stem": "sample", "created_at": NOW.isoformat()}
    stt_drive.save_state(stt_drive.state_path(env), {})
    watcher.run_once(client=drive, env=env, runner=lambda argv, child: code, now=NOW)
    assert len(calls) == 2
    assert len(audio_manifest.rows(audio_manifest.manifest_path(env))) == 1


@pytest.mark.parametrize("code", [0, 6])
@pytest.mark.parametrize("mode", ["missing", "nonzero", "malformed", "nan", "inf", "negative", "timeout", "oserror"])
def test_unprobeable_audio_records_unknown_without_changing_ingest(tmp_path: Path, code: int,
                                                                  mode: str,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    env = {**environment(tmp_path), "SPEECHTOTEXT_FFPROBE_BIN": str(tmp_path / "ffprobe")}
    drive = FakeDrive()
    probe_calls: list[list[str]] = []

    def probe(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert kwargs["timeout"] == watcher.stt_media.PROBE_TIMEOUT
        assert Path(argv[-1]).read_bytes() == PAYLOAD
        probe_calls.append(argv)
        if mode == "timeout":
            raise subprocess.TimeoutExpired(argv, watcher.stt_media.PROBE_TIMEOUT)
        if mode == "oserror":
            raise OSError("synthetic probe failure")
        output = {"nan": b'{"format":{"duration":"NaN"}}',
                  "inf": b'{"format":{"duration":"Infinity"}}',
                  "negative": b'{"format":{"duration":"-1"}}'}.get(mode, b"not-json")
        return subprocess.CompletedProcess(argv, int(mode == "nonzero"), output, b"")

    monkeypatch.setattr(watcher.stt_media, "resolve_ffprobe",
                        lambda env, **kwargs: None if mode == "missing" else Path(env["SPEECHTOTEXT_FFPROBE_BIN"]))
    monkeypatch.setattr(watcher.stt_media.subprocess, "run", probe)
    summary = watcher.run_once(client=drive, env=env, runner=lambda argv, child: code, now=NOW)
    assert summary == {"scanned": 1, "ingested": int(code == 0), "failed": int(code != 0), "skipped": 0}
    assert bool(stt_drive.load_state(stt_drive.state_path(env))) is (code == 0)
    row, = audio_manifest.rows(audio_manifest.manifest_path(env))
    assert row["duration_ms"] == 0 and row["domain"] == "meeting"
    assert len(probe_calls) == int(mode != "missing")
    assert not drive.downloads[0].parent.exists()


@pytest.mark.parametrize("code", [0, 6])
def test_manifest_failure_is_soft_and_does_not_change_marking(tmp_path: Path, code: int,
                                                            monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    def denied(entry: audio_manifest.ManifestEntry, env: dict[str, str]) -> bool:
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(audio_manifest, "record_manifest", denied)
    env = environment(tmp_path)
    drive = FakeDrive()
    summary = watcher.run_once(client=drive, env=env, runner=lambda argv, child: code, now=NOW)
    assert summary == {"scanned": 1, "ingested": int(code == 0), "failed": int(code != 0), "skipped": 0}
    assert bool(stt_drive.load_state(stt_drive.state_path(env))) is (code == 0)
    assert capsys.readouterr().err.count("SPEECHTOTEXT-MANIFEST-FAIL") == 1
    assert not audio_manifest.manifest_path(env).exists()
    assert not drive.downloads[0].parent.exists()


def test_checkout_manifest_refusal_does_not_block_success(tmp_path: Path,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _ = (checkout / ".git").write_text("gitdir: /unused", encoding="utf-8")
    env = {**environment(tmp_path), "STT_EVAL_ROOT": str(checkout / "state")}
    summary = watcher.run_once(client=FakeDrive(), env=env, runner=lambda argv, child: 0, now=NOW)
    assert summary["ingested"] == 1
    assert len(stt_drive.load_state(stt_drive.state_path(env))) == 1
    assert not (checkout / "state").exists()
    error = capsys.readouterr().err
    assert error.count("SPEECHTOTEXT-MANIFEST-FAIL") == 1
    assert "STT-EVAL-ROOT-REFUSED" in error


def test_runner_exception_keeps_original_failure_and_cleans_download(tmp_path: Path) -> None:
    def broken(argv: list[str], child: dict[str, str]) -> int:
        raise OSError("synthetic ingest failure")

    env = environment(tmp_path)
    drive = FakeDrive()
    with pytest.raises(OSError, match="synthetic ingest failure"):
        watcher.run_once(client=drive, env=env, runner=broken, now=NOW)
    assert not stt_drive.load_state(stt_drive.state_path(env))
    assert not drive.downloads[0].parent.exists()
    assert len(audio_manifest.rows(audio_manifest.manifest_path(env))) == 1


def manual_qa() -> None:
    """실제 run_once를 두 번 전사시키고 원장은 한 행인지 출력한다."""
    with tempfile.TemporaryDirectory(prefix="drive-watch-manifest-qa-") as directory:
        root = Path(directory)
        env = environment(root)
        drive = FakeDrive()
        calls: list[list[str]] = []

        def runner(argv: list[str], child: dict[str, str]) -> int:
            assert Path(argv[argv.index("--file") + 1]).read_bytes() == PAYLOAD
            calls.append(argv)
            return 0

        first = watcher.run_once(client=drive, env=env, runner=runner, now=NOW)
        count = len(audio_manifest.rows(audio_manifest.manifest_path(env)))
        assert first["ingested"] == count == 1
        print(f"drive_watch happy ingested=1 manifest_rows={count} domain=meeting uploads=0")
        stt_drive.save_state(stt_drive.state_path(env), {})
        second = watcher.run_once(client=drive, env=env, runner=runner, now=NOW)
        count = len(audio_manifest.rows(audio_manifest.manifest_path(env)))
        assert second["ingested"] == count == 1 and len(calls) == 2
        print(f"drive_watch replay ingest_calls={len(calls)} manifest_rows={count} duplicates=0 uploads=0")
        third = watcher.run_once(client=drive, env=env, runner=runner, now=NOW)
        assert third["ingested"] == 0 and len(calls) == 2
        assert all(not path.parent.exists() for path in drive.downloads)
        print("drive_watch processed_retry ingested=0 manifest_rows=1")
    assert not root.exists()
    print("drive_watch cleanup temporary_runtime_removed=true network_calls=0")


if __name__ == "__main__":
    manual_qa()
