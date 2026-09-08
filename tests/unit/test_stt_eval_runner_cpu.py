"""CPU-only 정책은 실제 자식 실행 앞에서 후보만 제외하고 ASR을 보존한다."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from automation.plaud_sync import audio_manifest
from automation.stt_eval import runner
from tests.unit.test_speechtotext_drive_watch_manifest import (
    NOW, PAYLOAD, FakeDrive as MeetingDrive, environment, watcher,
)
from automation.stt_eval.runner_inputs import read_jsonl
from tests.unit.test_stt_eval_gpu_guard import stub_environment
from tests.unit.test_stt_eval_runner import CLI, FakeDrive


@pytest.fixture
def setup(tmp_path: Path):
    env = {**stub_environment(tmp_path), "CREDENTIAL_SENTINEL": "forwarded",
           "CALLS": str(tmp_path / "calls"), "ACTIVE": str(tmp_path / "active"),
           "STT_ENGINES_CPU_MAX_MS": "600000"}
    cli = tmp_path / "cli.py"
    cli.write_text(CLI)
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    base = (("SPEECHTOTEXT_WHISPER_MODEL", str(model)), ("SPEECHTOTEXT_TEST_HASH", "a" * 64))
    drive = FakeDrive()
    manifest = [{"drive_file_id": "first", "audio_sha256": hashlib.sha256(drive.payloads["first"]).hexdigest(),
                 "duration_ms": 600001}]
    return env, cli, base, drive, manifest


def execute(tmp_path: Path, setup, overrides=()):
    env, cli, base, drive, manifest = setup
    return runner.run(manifest, (runner.Candidate("test", (*base, *overrides)),), tmp_path / "eval",
                      drive=drive, cli=(sys.executable, str(cli)), tmp=tmp_path / "tmp", env=env)


@pytest.mark.parametrize("overrides", [(("SPEECHTOTEXT_DIARIZE_BACKEND", "pyannote"),),
                                      (("SPEECHTOTEXT_DIARIZE_BACKEND", "sherpa"),),
                                      (("SPEECHTOTEXT_ALIGN_BACKEND", "whisperx"),)])
@pytest.mark.parametrize("maximum", ["600000", "", "1000"])
def test_cpu_only_long_diarization_skips_before_cli_and_download(tmp_path, setup, capsys, overrides, maximum):
    env, _, _, drive, _ = setup
    env["STT_ENGINES_CPU_MAX_MS"] = maximum
    assert execute(tmp_path, setup, overrides) == 0
    assert "DIARIZE-SKIP reason=cpu-only" in capsys.readouterr().out
    assert not Path(env["CALLS"]).exists()
    assert drive.downloads == []
    row = read_jsonl(tmp_path / "eval" / "runs.jsonl")[0]
    assert row["reason"] == "cpu-only"
    assert row["status"] == "failed" and row["rc"] is None and row["config_sha256"] is None
    assert not (tmp_path / "eval" / "hyp").exists()
    assert execute(tmp_path, setup, overrides) == 0  # 성공으로 재사용하지 않는다.
    assert len(read_jsonl(tmp_path / "eval" / "runs.jsonl")) == 2


@pytest.mark.parametrize("duration", [660000, 0])
def test_meeting_manifest_cpu_limit_before_cli(tmp_path, setup, duration):
    env, cli, base, drive, _ = setup
    probe = tmp_path / "ffprobe"
    probe.write_text(
        f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n"
        'assert sys.argv[1:-1] == ["-v", "error", "-show_entries", "format=duration", "-of", "json"]\n'
        f"assert Path(sys.argv[-1]).read_bytes() == {PAYLOAD!r}\n"
        f"print(json.dumps({{'format': {{'duration': '{duration / 1000}'}}}}))\n"
    )
    probe.chmod(0o700)
    watch_env = {**environment(tmp_path), "SPEECHTOTEXT_FFPROBE_BIN": str(probe)}
    summary = watcher.run_once(client=MeetingDrive(), env=watch_env,
                               runner=lambda argv, child: 0, now=NOW)
    assert summary["ingested"] == 1
    manifest = audio_manifest.manifest_path(watch_env)
    row, = read_jsonl(manifest)
    drive.payloads = {"synthetic-drive-file": PAYLOAD}
    candidate = runner.Candidate("diar", (*base, ("SPEECHTOTEXT_DIARIZE_BACKEND", "pyannote")))
    assert runner.run(manifest, (candidate,), tmp_path / "eval", drive=drive, cli=cli,
                      tmp=tmp_path / "tmp", env=env) == 0
    calls = read_jsonl(Path(env["CALLS"])) if Path(env["CALLS"]).exists() else []
    print(f"meeting source_duration_ms={duration} manifest_duration_ms={row['duration_ms']} "
          f"transcription_cli_calls={len(calls)}")
    assert len(calls) == 0
    assert row["duration_ms"] == duration
    assert row["domain"] == "meeting"
    assert drive.downloads == []
    assert read_jsonl(tmp_path / "eval" / "runs.jsonl")[0]["reason"] == "cpu-only"


@pytest.mark.parametrize("duration", [599999, 600000])
def test_cpu_limit_is_strictly_greater_than(tmp_path, setup, duration):
    setup[4][0]["duration_ms"] = duration
    assert execute(tmp_path, setup, (("SPEECHTOTEXT_DIARIZE_BACKEND", "pyannote"),)) == 0
    assert len(read_jsonl(Path(setup[0]["CALLS"]))) == 1


@pytest.mark.parametrize("mode,duration", [("asr", 600001), ("asr", 0),
                                          ("unset", 600001), ("unset", 0), ("raised", 600001)])
def test_cpu_limit_does_not_disable_asr_or_cuda_candidates(tmp_path, setup, mode, duration):
    setup[4][0]["duration_ms"] = duration
    env = setup[0]
    overrides = () if mode == "asr" else (("SPEECHTOTEXT_DIARIZE_BACKEND", "pyannote"),)
    env["SPEECHTOTEXT_DIARIZE_BACKEND"] = "pyannote"  # 부모 기본값은 ASR 후보를 분리 후보로 바꾸지 않는다.
    if mode == "unset":
        del env["STT_ENGINES_CPU_MAX_MS"]
    elif mode == "raised":
        env["STT_ENGINES_CPU_MAX_MS"] = "700000"
    assert execute(tmp_path, setup, overrides) == 0
    assert len(read_jsonl(Path(env["CALLS"]))) == 1


@pytest.mark.parametrize("maximum", ["-1", "0", "1.5", "invalid"])
def test_invalid_cpu_limit_is_input_error_before_effects(tmp_path, setup, maximum, capsys):
    setup[0]["STT_ENGINES_CPU_MAX_MS"] = maximum
    assert execute(tmp_path, setup) == 2
    assert "STT-EVAL-INPUT-INVALID" in capsys.readouterr().out
    assert setup[3].downloads == []
    assert not Path(setup[0]["CALLS"]).exists()


def test_skipped_candidate_does_not_block_next_asr(tmp_path, setup):
    env, cli, base, drive, manifest = setup
    configs = (runner.Candidate("diar", (*base, ("SPEECHTOTEXT_DIARIZE_BACKEND", "pyannote"))),
               runner.Candidate("asr", base))
    assert runner.run(manifest, configs, tmp_path / "eval", drive=drive, cli=cli,
                      tmp=tmp_path / "tmp", env=env) == 0
    rows = read_jsonl(tmp_path / "eval" / "runs.jsonl")
    assert [(row["label"], row["reason"]) for row in rows] == [("diar", "cpu-only"), ("asr", "")]
    assert len(Path(env["CALLS"]).read_text().splitlines()) == 1
    assert json.loads(Path(env["CALLS"]).read_text())["prompt"] == ""
