"""Tests for the offline sherpa-onnx speaker diarization wrapper."""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

# stt_diarize 는 형제 모듈(stt_split)을 맨이름으로 가져온다 — 배포 노드에서 scripts/ 가
# sys.path 인 채로 실행되기 때문이다. 테스트도 같은 경로로 붙여야 그 import 가 풀린다.
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "speechtotext" / "scripts"))

import stt_diarize  # noqa: E402


@dataclass(frozen=True, slots=True)
class Sentence:
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    speaker: str = ""


# Representative lines emitted after sherpa-onnx's configuration dump and Started line.
SHERPA_OUTPUT = """\
2025-01-01 00:00:00.000 INFO [speaker-diarization.cc:123] Started
0.031 -- 3.456 speaker_00
3.456 -- 7.892 speaker_01
7.892 -- 11.103 speaker_00
11.103 -- 14.950 speaker_02
14.950 -- 19.042 speaker_03
19.042 -- 23.175 speaker_01
23.175 -- 27.306 speaker_02
27.306 -- 31.440 speaker_03
31.440 -- 35.571 speaker_00
35.571 -- 39.702 speaker_01
Real time factor (RTF): 0.012
"""


def _files(tmp_path: Path) -> dict[str, str]:
    binary = tmp_path / "bin" / "diarize"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    _ = binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    segmentation = tmp_path / "segmentation.onnx"
    embedding = tmp_path / "embedding.onnx"
    segmentation.touch()
    embedding.touch()
    return {
        "SPEECHTOTEXT_DIARIZE_BIN": str(binary),
        "SPEECHTOTEXT_DIARIZE_SEGMENTATION": str(segmentation),
        "SPEECHTOTEXT_DIARIZE_EMBEDDING": str(embedding),
    }


def test_resolve_toolchain_requires_all_files_and_uses_defaults(tmp_path: Path) -> None:
    assert stt_diarize.resolve_toolchain({}) is None
    env = _files(tmp_path)
    assert stt_diarize.resolve_toolchain({**env, "SPEECHTOTEXT_DIARIZE_EMBEDDING": "bad"}) is None
    toolchain = stt_diarize.resolve_toolchain(
        {
            **env,
            "SPEECHTOTEXT_DIARIZE_THRESHOLD": "bad",
            "SPEECHTOTEXT_DIARIZE_THREADS": "0",
            "SPEECHTOTEXT_DIARIZE_TIMEOUT": "no",
        }
    )
    assert toolchain is not None
    assert (toolchain.threshold, toolchain.threads, toolchain.timeout) == (
        0.9, min(os.cpu_count() or 1, 8), 3600.0,
    )


def test_parse_output_extracts_sorted_turns_and_ignores_noise() -> None:
    turns = stt_diarize.parse_output(SHERPA_OUTPUT + "\n9 -- nope speaker_05\n")
    assert turns[0] == stt_diarize.Turn(31, 3456, 0)
    assert len(turns) == 10
    assert stt_diarize.parse_output("Started\nRTF 0.1\n") == ()


def test_diarize_passes_flags_and_library_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _files(tmp_path)
    binary = Path(env["SPEECHTOTEXT_DIARIZE_BIN"])
    log = tmp_path / "child.json"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "Path = __import__('pathlib').Path\n"
        "Path(os.environ['DIARIZE_LOG']).write_text(json.dumps({'argv': sys.argv[1:], 'ld': os.environ['LD_LIBRARY_PATH']}))\n"
        "print('0.031 -- 3.456 speaker_00')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DIARIZE_LOG", str(log))
    # The default thread count is min(cpu_count, 8), which differs between this
    # workstation and a 2-vCPU CI runner; pin a non-default value so the assertion
    # proves the flag is passed through rather than echoing the host's core count.
    toolchain = stt_diarize.resolve_toolchain({**env, "SPEECHTOTEXT_DIARIZE_THREADS": "3"})
    assert toolchain is not None
    turns = stt_diarize.diarize(tmp_path / "audio.wav", toolchain)
    recorded = json.loads(log.read_text(encoding="utf-8"))
    assert turns == (stt_diarize.Turn(31, 3456, 0),)
    assert "--segmentation.pyannote-model=" + env["SPEECHTOTEXT_DIARIZE_SEGMENTATION"] in recorded["argv"]
    assert "--embedding.model=" + env["SPEECHTOTEXT_DIARIZE_EMBEDDING"] in recorded["argv"]
    assert "--segmentation.num-threads=3" in recorded["argv"]
    assert "--embedding.num-threads=3" in recorded["argv"]
    assert "--clustering.cluster-threshold=0.9" in recorded["argv"]
    assert recorded["ld"].split(":")[0] == str(tmp_path / "lib")
    stt_diarize.diarize(tmp_path / "audio.wav", toolchain, num_speakers=4)
    assert "--clustering.num-clusters=4" in json.loads(log.read_text(encoding="utf-8"))["argv"]


def test_diarize_raises_for_binary_failure(tmp_path: Path) -> None:
    env = _files(tmp_path)
    binary = Path(env["SPEECHTOTEXT_DIARIZE_BIN"])
    binary.write_text("#!/bin/sh\necho broken >&2\nexit 1\n", encoding="utf-8")
    toolchain = stt_diarize.resolve_toolchain(env)
    assert toolchain is not None
    with pytest.raises(stt_diarize.DiarizeError, match="broken"):
        stt_diarize.diarize(tmp_path / "audio.wav", toolchain)


def test_assign_prefers_overlap_then_nearest_and_inherits() -> None:
    turns = (
        stt_diarize.Turn(0, 1000, 3),
        stt_diarize.Turn(3000, 4000, 1),
    )
    assigned = stt_diarize.assign(
        (
            Sentence("first", 100, 900),
            Sentence("overlap", 700, 3500),
            Sentence("nearest", 4800, 4900),
            Sentence("untimed"),
            Sentence("far", 9000, 9100),
        ),
        turns,
    )
    assert tuple(sentence.speaker for sentence in assigned) == (
        "화자1", "화자2", "화자2", "화자2", "화자2",
    )
