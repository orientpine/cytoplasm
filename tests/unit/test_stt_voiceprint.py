"""voice catalog ③ — 임베딩 추출의 I/O 절반.

노드는 aarch64 이고 sherpa-onnx 의 파이썬 휠도 numpy 도 없다. 그러나 화자 분리가 이미 쓰는
`libsherpa-onnx-c-api.so` 가 거기 있으므로 stdlib `ctypes` 로 **같은 임베딩 모델**을 부른다.
서드파티는 0 이다. 실제 ctypes 호출은 노드 실측이 증명하고, 여기서는 wav 판독·구간 절단과
CLI 계약을 가짜 추출기로 고정한다.
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "speechtotext" / "scripts"))

import stt_voiceprint  # noqa: E402


def _wav(path: Path, samples: list[int], *, rate: int = 16_000, channels: int = 1, width: int = 2) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        payload = b"".join(
            int(value).to_bytes(width, "little", signed=True) for value in samples
        )
        handle.writeframes(payload * channels)
    return path


def test_read_pcm_scales_every_frame_to_the_unit_range(tmp_path: Path) -> None:
    wav = _wav(tmp_path / "a.wav", [0, 16_384, -16_384, 32_767])

    samples = stt_voiceprint.read_pcm(wav, None)

    assert len(samples) == 4
    assert samples[0] == pytest.approx(0.0)
    assert samples[1] == pytest.approx(0.5)
    assert samples[2] == pytest.approx(-0.5)


def test_read_pcm_concatenates_only_the_requested_spans(tmp_path: Path) -> None:
    wav = _wav(tmp_path / "b.wav", list(range(16_000)))

    samples = stt_voiceprint.read_pcm(wav, [(100, 200), (500, 550)])

    assert len(samples) == 1_600 + 800
    assert samples[0] == pytest.approx(1_600 / 32_768)
    assert samples[1_600] == pytest.approx(8_000 / 32_768)


def test_read_pcm_clamps_a_span_that_runs_past_the_recording(tmp_path: Path) -> None:
    wav = _wav(tmp_path / "c.wav", list(range(16_000)))

    samples = stt_voiceprint.read_pcm(wav, [(900, 5_000)])

    assert len(samples) == 1_600


def test_read_pcm_refuses_an_empty_selection(tmp_path: Path) -> None:
    wav = _wav(tmp_path / "d.wav", list(range(1_600)))

    with pytest.raises(stt_voiceprint.VoiceprintError, match="VOICEPRINT-EMPTY"):
        stt_voiceprint.read_pcm(wav, [(500, 600)])


def test_read_pcm_refuses_anything_but_16k_mono_pcm16(tmp_path: Path) -> None:
    stereo = _wav(tmp_path / "e.wav", [0, 1, 2, 3], channels=2)
    resampled = _wav(tmp_path / "f.wav", [0, 1, 2, 3], rate=8_000)

    for path in (stereo, resampled):
        with pytest.raises(stt_voiceprint.VoiceprintError, match="VOICEPRINT-WAV-FORMAT"):
            stt_voiceprint.read_pcm(path, None)


def test_resolve_toolchain_needs_the_c_api_beside_the_diarizer(tmp_path: Path) -> None:
    binary = tmp_path / "bin" / "sherpa-onnx-offline-speaker-diarization"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"")
    model = tmp_path / "models" / "embedding.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"")
    env = {
        "SPEECHTOTEXT_DIARIZE_BIN": str(binary),
        "SPEECHTOTEXT_DIARIZE_EMBEDDING": str(model),
    }

    assert stt_voiceprint.resolve_toolchain(env) is None

    library = tmp_path / "lib"
    library.mkdir()
    (library / stt_voiceprint.C_API_LIBRARY).write_bytes(b"")

    resolved = stt_voiceprint.resolve_toolchain(env)

    assert resolved is not None
    assert (resolved.library_dir, resolved.model) == (library, model)
    assert stt_voiceprint.resolve_toolchain({**env, "SPEECHTOTEXT_DIARIZE_BACKEND": "pyannote"}) is None
    assert stt_voiceprint.resolve_toolchain({}) is None


class _FakeExtractor:
    """dim 과 compute 만 흉내낸다 — 계약은 CLI 의 JSON 이지 모델이 아니다."""

    dim = 3

    def __init__(self, toolchain: stt_voiceprint.Toolchain) -> None:
        self.toolchain = toolchain

    def compute(self, samples: object) -> tuple[float, ...]:
        return (0.5, 0.25, 0.125)

    def close(self) -> None:
        return None


def _job(tmp_path: Path, jobs: list[dict[str, object]]) -> Path:
    model = tmp_path / "model.onnx"
    model.write_bytes(b"onnx")
    payload = {
        "library_dir": str(tmp_path / "lib"),
        "model": str(model),
        "threads": 2,
        "jobs": jobs,
    }
    path = tmp_path / "job.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_cli_returns_one_json_line_with_every_embedding(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wav = _wav(tmp_path / "one.wav", list(range(16_000)))
    job = _job(tmp_path, [{"id": "화자1", "wav": str(wav), "spans": [[0, 500]]}])

    code = stt_voiceprint.main(["--job", str(job)], extractor_factory=_FakeExtractor)

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert code == 0
    assert payload["dim"] == 3
    assert payload["embeddings"]["화자1"] == [0.5, 0.25, 0.125]
    assert payload["failed"] == {}
    assert len(payload["model_sha256"]) == 64


def test_cli_keeps_going_when_one_job_cannot_be_read(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = _wav(tmp_path / "good.wav", list(range(16_000)))
    bad = _wav(tmp_path / "bad.wav", [0, 1, 2, 3], rate=8_000)
    job = _job(
        tmp_path,
        [
            {"id": "화자1", "wav": str(good), "spans": None},
            {"id": "화자2", "wav": str(bad), "spans": None},
            {"id": "화자3", "wav": str(tmp_path / "missing.wav"), "spans": None},
        ],
    )

    code = stt_voiceprint.main(["--job", str(job)], extractor_factory=_FakeExtractor)

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0
    assert list(payload["embeddings"]) == ["화자1"]
    assert "VOICEPRINT-WAV-FORMAT" in payload["failed"]["화자2"]
    assert payload["failed"]["화자3"]


def test_cli_fails_when_no_job_produced_an_embedding(tmp_path: Path) -> None:
    bad = _wav(tmp_path / "bad.wav", [0, 1], rate=8_000)
    job = _job(tmp_path, [{"id": "화자1", "wav": str(bad), "spans": None}])

    assert stt_voiceprint.main(["--job", str(job)], extractor_factory=_FakeExtractor) == 1


def test_cli_reports_a_library_that_will_not_load(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wav = _wav(tmp_path / "one.wav", list(range(16_000)))
    job = _job(tmp_path, [{"id": "화자1", "wav": str(wav), "spans": None}])

    def _refuse(toolchain: stt_voiceprint.Toolchain) -> _FakeExtractor:
        raise stt_voiceprint.VoiceprintError("VOICEPRINT-LIB-FAIL OSError")

    code = stt_voiceprint.main(["--job", str(job)], extractor_factory=_refuse)

    assert code == 1
    assert "VOICEPRINT-LIB-FAIL" in capsys.readouterr().err


def test_cli_refuses_a_job_file_it_cannot_parse(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    missing = tmp_path / "nope.json"
    shapeless = tmp_path / "shapeless.json"
    shapeless.write_text(json.dumps({"jobs": []}), encoding="utf-8")

    for path in (broken, missing, shapeless):
        assert stt_voiceprint.main(["--job", str(path)], extractor_factory=_FakeExtractor) == 2
