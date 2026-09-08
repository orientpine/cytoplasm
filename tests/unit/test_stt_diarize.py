"""Tests for the offline sherpa-onnx speaker diarization wrapper."""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from skills.speechtotext.scripts.stt_client import Transcription

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
        1.0, min(os.cpu_count() or 1, 8), 3600.0,
    )
    # on 은 불안정한 0.5 초 미만 조각을 버리고(가짜 화자 방지), off 는 같은 화자의 간격을
    # 재귀적으로 이어 붙인다. off=0.8 이 4.5분 녹음을 12턴·중앙값 16.65초로 만들었으므로
    # 병합은 끄고(0.0) 조각 가드만 남긴다 — 2026-09-07 노드 실측, 실질 화자 수 불변.
    assert (toolchain.min_duration_on, toolchain.min_duration_off) == (0.5, 0.0)
    # 화자 수를 아는 녹음에서는 그것이 임계값 추정을 이긴다. 선언이 없으면 None.
    assert toolchain.speakers is None


def test_diarize_reclusters_at_the_cap_instead_of_discarding_speakers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """상한을 넘겼다고 화자 정보를 통째로 버리면 소유자는 '화자 없는 문서'를 받는다.

    실측 2026-09-05: 2인 녹음이 화자 102명으로 나왔다(그 배포본에는 상한이 아예 없었다).
    상한이 있었어도 결과는 라벨 0개였을 뿐이고, 그것은 뭉뚱그린 화자보다 나쁘다.
    sherpa 의 --clustering.num-clusters 는 상한처럼 동작한다 — 133초 2인 샘플에서
    k=3·4·6·8 이 전부 화자 2를 냈다 — 그러니 버리는 대신 상한으로 다시 묶는다.
    """
    env = _files(tmp_path)
    binary = Path(env["SPEECHTOTEXT_DIARIZE_BIN"])
    log = tmp_path / "calls.json"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "log = pathlib.Path(os.environ['DIARIZE_LOG'])\n"
        "calls = json.loads(log.read_text()) if log.exists() else []\n"
        "calls.append(sys.argv[1:])\n"
        "log.write_text(json.dumps(calls))\n"
        "speakers = 9 if len(calls) == 1 else 3\n"
        "for index in range(speakers):\n"
        "    print('%d -- %d speaker_%02d' % (index, index + 1, index))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DIARIZE_LOG", str(log))
    toolchain = stt_diarize.resolve_toolchain(env)
    assert toolchain is not None

    turns = stt_diarize.diarize(tmp_path / "audio.wav", toolchain)

    calls = json.loads(log.read_text(encoding="utf-8"))
    assert len({turn.speaker for turn in turns}) == 3
    assert len(calls) == 2
    assert any(arg.startswith("--clustering.cluster-threshold=") for arg in calls[0])
    assert "--clustering.num-clusters=8" in calls[1]
    assert capsys.readouterr().err == (
        "DIARIZE-CLUSTERS speakers=9 threshold=1.0 turns=9\n"
        "DIARIZE-RECLUSTERED speakers=9 max=8\n"
    )


def test_diarize_gives_up_only_when_the_repair_also_oversegments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """다시 묶어도 상한을 넘으면 그때는 라벨 없이 간다 — 마지막 수단은 그대로 남는다."""
    env = _files(tmp_path)
    binary = Path(env["SPEECHTOTEXT_DIARIZE_BIN"])
    binary.write_text(
        "#!/bin/sh\n" + "\n".join(f"echo '0 -- 1 speaker_{speaker:02d}'" for speaker in range(9)),
        encoding="utf-8",
    )
    toolchain = stt_diarize.resolve_toolchain(env)
    assert toolchain is not None

    assert stt_diarize.diarize(tmp_path / "audio.wav", toolchain) == ()

    assert capsys.readouterr().err == (
        "DIARIZE-CLUSTERS speakers=9 threshold=1.0 turns=9\n"
        "DIARIZE-RECLUSTERED speakers=9 max=8\n"
        "DIARIZE-OVERSEGMENTED speakers=9 max=8\n"
    )


def test_diarize_passes_the_fragment_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.5 초 미만 조각과 0.8 초 미만 간격은 화자가 아니라 분리기의 떨림이다."""
    env = _files(tmp_path)
    binary = Path(env["SPEECHTOTEXT_DIARIZE_BIN"])
    log = tmp_path / "child.json"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['DIARIZE_LOG']).write_text(json.dumps(sys.argv[1:]))\n"
        "print('0.031 -- 3.456 speaker_00')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DIARIZE_LOG", str(log))
    toolchain = stt_diarize.resolve_toolchain(env)
    assert toolchain is not None

    _ = stt_diarize.diarize(tmp_path / "audio.wav", toolchain)

    argv = json.loads(log.read_text(encoding="utf-8"))
    assert "--min-duration-on=0.5" in argv
    assert "--min-duration-off=0" in argv


def test_diarize_obeys_a_declared_speaker_count_without_repairing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """소유자가 화자 수를 아는 녹음에서는 그 선언이 임계값 추정을 이긴다.

    노드 실측(133초 2인): --clustering.num-clusters=2 는 정확히 2를 냈고 임계값
    1.3 은 같은 오디오를 화자 1 로 뭉갰다. 선언이 있으면 상한 보수도 돌지 않는다 —
    소유자의 사실이 우리 추정보다 앞선다.
    """
    env = _files(tmp_path)
    binary = Path(env["SPEECHTOTEXT_DIARIZE_BIN"])
    log = tmp_path / "calls.json"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "log = pathlib.Path(os.environ['DIARIZE_LOG'])\n"
        "calls = json.loads(log.read_text()) if log.exists() else []\n"
        "calls.append(sys.argv[1:])\n"
        "log.write_text(json.dumps(calls))\n"
        "for index in range(9):\n"
        "    print('%d -- %d speaker_%02d' % (index, index + 1, index))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DIARIZE_LOG", str(log))
    toolchain = stt_diarize.resolve_toolchain({**env, "SPEECHTOTEXT_DIARIZE_SPEAKERS": "2"})
    assert toolchain is not None

    turns = stt_diarize.diarize(tmp_path / "audio.wav", toolchain)

    calls = json.loads(log.read_text(encoding="utf-8"))
    assert len(calls) == 1
    assert "--clustering.num-clusters=2" in calls[0]
    assert not any(arg.startswith("--clustering.cluster-threshold=") for arg in calls[0])
    assert len({turn.speaker for turn in turns}) == 9
    # 선언 경로에서도 진단은 남는다 — 무엇이 나왔는지가 임계값 판단의 근거다.
    assert capsys.readouterr().err == "DIARIZE-CLUSTERS speakers=9 threshold=1.0 turns=9\n"

def test_diarize_accepts_exactly_max_speakers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = _files(tmp_path)
    binary = Path(env["SPEECHTOTEXT_DIARIZE_BIN"])
    binary.write_text(
        "#!/bin/sh\n" + "\n".join(f"echo '0 -- 1 speaker_{speaker:02d}'" for speaker in range(8)),
        encoding="utf-8",
    )
    toolchain = stt_diarize.resolve_toolchain(env)
    assert toolchain is not None
    assert len(stt_diarize.diarize(tmp_path / "audio.wav", toolchain)) == 8
    assert capsys.readouterr().err == "DIARIZE-CLUSTERS speakers=8 threshold=1.0 turns=8\n"


def test_diarize_prints_cluster_diagnostics_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Given: sherpa emits two clusters across three turns.
    env = _files(tmp_path)
    binary = Path(env["SPEECHTOTEXT_DIARIZE_BIN"])
    binary.write_text(
        "#!/bin/sh\n"
        "echo '0 -- 1 speaker_00'\n"
        "echo '1 -- 2 speaker_01'\n"
        "echo '2 -- 3 speaker_00'\n",
        encoding="utf-8",
    )
    toolchain = stt_diarize.resolve_toolchain({**env, "SPEECHTOTEXT_DIARIZE_THRESHOLD": "1.25"})
    assert toolchain is not None

    # When: diarization accepts the parsed turns.
    assert len(stt_diarize.diarize(tmp_path / "audio.wav", toolchain)) == 3

    # Then: the permanent machine-readable cluster marker is written once.
    assert capsys.readouterr().err == "DIARIZE-CLUSTERS speakers=2 threshold=1.25 turns=3\n"


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
    assert "--clustering.cluster-threshold=1.0" in recorded["argv"]
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


def test_default_sherpa_argv_is_byte_identical(tmp_path: Path) -> None:
    """백엔드 미설정의 기존 argv 전체와 순서를 변경 전에 고정한다."""
    env = _files(tmp_path)
    toolchain = stt_diarize.resolve_toolchain({**env, "SPEECHTOTEXT_DIARIZE_THREADS": "3"})
    assert toolchain is not None
    wav = tmp_path / "audio.wav"
    expected = [
        env["SPEECHTOTEXT_DIARIZE_BIN"],
        "--segmentation.pyannote-model=" + env["SPEECHTOTEXT_DIARIZE_SEGMENTATION"],
        "--embedding.model=" + env["SPEECHTOTEXT_DIARIZE_EMBEDDING"],
        "--segmentation.num-threads=3", "--embedding.num-threads=3",
        "--min-duration-on=0.5", "--min-duration-off=0",
        "--clustering.cluster-threshold=1.0", str(wav),
    ]
    assert stt_diarize._argv(wav, toolchain, None) == expected
    expected[-2] = "--clustering.num-clusters=2"
    assert stt_diarize._argv(wav, toolchain, 2) == expected


def test_assign_prefers_overlap_then_nearest_but_never_inherits() -> None:
    """겹침·최근접은 유지하고, 옛 상속 대상인 무시각·먼 문장은 화자0으로 바꾼다."""
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
        "화자1", "화자2", "화자2", "화자0", "화자0",
    )


def test_assign_unknown_has_explicit_attribution_and_no_stale_speaker() -> None:
    import stt_attribute
    import stt_blocks

    assigned = stt_diarize.assign((
        stt_blocks.TimedSentence("합성 시작.", 0, 1000),
        stt_blocks.TimedSentence("합성 무시각.", speaker="화자9",
                                 attribution=stt_attribute.SpeakerTag("SPEAKER", ("화자9",))),
        stt_blocks.TimedSentence("합성 먼 문장.", 10000, 11000),
    ), (stt_diarize.Turn(0, 1000, 7),))
    assert assigned[0].attribution == stt_attribute.SpeakerTag("SPEAKER", ("화자1",))
    assert all(s.speaker == "화자0" and s.attribution == stt_attribute.SpeakerTag("UNKNOWN")
               for s in assigned[1:])
    assert "화자0 · UNKNOWN" in stt_blocks.render(stt_blocks.group(assigned))


def test_assign_without_turns_marks_unknown_but_never_attributes_gap() -> None:
    import stt_attribute
    import stt_blocks
    import stt_window

    marker = stt_window.gap_marker(stt_window.Window(0, 0, 1000), until=1000)
    gap = stt_blocks.TimedSentence(marker, 0, 1000)
    assigned = stt_diarize.assign((stt_blocks.TimedSentence("합성 발화."), gap), ())
    assert assigned[0].speaker == "화자0"
    assert assigned[0].attribution == stt_attribute.SpeakerTag("UNKNOWN")
    assert assigned[1] == gap


def _pyannote_cli(tmp_path: Path, *, exit_code: int = 0) -> tuple[dict[str, str], Path]:
    """격리 CLI 대역은 실제 자식 프로세스로 argv와 겹침 turn을 내보낸다."""
    binary = tmp_path / "stt-engines"
    log = tmp_path / "argv.jsonl"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"with open({str(log)!r}, 'a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "print('0 -- 2 speaker_00')\n"
        "print('1 -- 3 speaker_01')\n"
        + ("print('STT-ENGINES-NO-TOKEN', file=sys.stderr)\n" if exit_code == 4 else "")
        + f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return {"SPEECHTOTEXT_DIARIZE_BACKEND": "pyannote",
            "SPEECHTOTEXT_DIARIZE_BIN": str(binary)}, log


@pytest.mark.parametrize("mode", ["regular", "exclusive"])
@pytest.mark.parametrize("declared,override,flags", [
    (None, None, ["--min", "1", "--max", "1"]),
    ("2", None, ["--num-speakers", "2"]),
    ("2", 3, ["--num-speakers", "3"]),
])
def test_pyannote_argv_overlap_and_no_reexecution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: str,
    declared: str | None, override: int | None, flags: list[str],
) -> None:
    from stt_attribute import AttributionPolicy, attribute_words
    from stt_sentence import TimedWord

    env, log = _pyannote_cli(tmp_path)
    env["SPEECHTOTEXT_DIARIZE_MAX_SPEAKERS"] = "1"
    if mode != "regular":
        env["SPEECHTOTEXT_DIARIZE_MODE"] = mode
    if declared is not None:
        env["SPEECHTOTEXT_DIARIZE_SPEAKERS"] = declared
    toolchain = stt_diarize.resolve_toolchain(env)
    assert toolchain is not None
    assert toolchain.backend == "pyannote"
    assert toolchain.segmentation is None and toolchain.embedding is None
    wav = tmp_path / "audio.wav"
    turns = stt_diarize.diarize(wav, toolchain, num_speakers=override)
    assert turns == (stt_diarize.Turn(0, 2000, 0), stt_diarize.Turn(1000, 3000, 1))
    assert [json.loads(line) for line in log.read_text().splitlines()] == [
        ["diarize", "--wav", str(wav), "--mode", mode, *flags],
    ]
    assigned = attribute_words((TimedWord("합성", 1200, 1800),), turns, policy=AttributionPolicy())
    assert assigned[0].tag.kind == "OVERLAP"
    assert assigned[0].simultaneous_ms == 600
    stderr = capsys.readouterr().err
    assert "DIARIZE-CLUSTERS speakers=2" in stderr
    assert "DIARIZE-RECLUSTERED" not in stderr


@pytest.mark.parametrize("backend", ["sherpa", "pyannote"])
def test_explicit_backend_selects_cli(tmp_path: Path, backend: str) -> None:
    env = _files(tmp_path)
    toolchain = stt_diarize.resolve_toolchain({**env, "SPEECHTOTEXT_DIARIZE_BACKEND": backend})
    assert toolchain is not None
    assert toolchain.backend == backend


@pytest.mark.parametrize("env,marker", [
    ({"SPEECHTOTEXT_DIARIZE_BACKEND": "typo"}, "DIARIZE-FAIL backend=typo"),
    ({"SPEECHTOTEXT_DIARIZE_BACKEND": "pyannote"}, "DIARIZE-FAIL backend=pyannote"),
    ({"SPEECHTOTEXT_DIARIZE_BACKEND": "pyannote", "SPEECHTOTEXT_DIARIZE_BIN": "missing"},
     "DIARIZE-FAIL backend=pyannote"),
])
def test_invalid_or_missing_backend_fails_soft(
    env: dict[str, str], marker: str, capsys: pytest.CaptureFixture[str],
) -> None:
    assert stt_diarize.resolve_toolchain(env) is None
    assert marker in capsys.readouterr().err


def test_pyannote_bad_mode_fails_soft(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env, log = _pyannote_cli(tmp_path)
    assert stt_diarize.resolve_toolchain({**env, "SPEECHTOTEXT_DIARIZE_MODE": "typo"}) is None
    assert "DIARIZE-FAIL backend=pyannote mode=typo" in capsys.readouterr().err
    assert not log.exists()


def _local_transcription(tmp_path: Path, env: dict[str, str]) -> Transcription:
    """전사와 화자 분리의 실제 subprocess 경계를 모두 통과하는 합성 입력이다."""
    import stt_audio
    import stt_local

    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text('#!/bin/sh\nfor a in "$@"; do last="$a"; done\nprintf RIFF > "$last"\n')
    ffmpeg.chmod(0o755)
    whisper = tmp_path / "whisper"
    payload = json.dumps({"transcription": [{"text": "합성 검증.",
                                             "offsets": {"from": 0, "to": 1000}}]})
    whisper.write_text(
        f"#!{sys.executable}\nimport pathlib, sys\n"
        f"pathlib.Path(sys.argv[sys.argv.index('-of')+1]+'.json').write_text({payload!r})\n",
    )
    whisper.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_bytes(b"synthetic model")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF0000")
    local = stt_local.resolve_toolchain({
        "SPEECHTOTEXT_FFMPEG_BIN": str(ffmpeg), "SPEECHTOTEXT_WHISPER_BIN": str(whisper),
        "SPEECHTOTEXT_WHISPER_MODEL": str(model),
    })
    assert local is not None
    checked = stt_audio.CheckedAudio(path=audio, suffix=".wav", mime="audio/wav", size_bytes=8)
    return stt_local.transcribe(checked, local, diarizer=stt_diarize.resolve_toolchain(env))


@pytest.mark.parametrize("failure", ["rc1", "rc4", "removed", "permission", "missing", "typo"])
def test_pyannote_failure_never_stops_transcription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: str,
) -> None:
    monkeypatch.setenv("SPEECHTOTEXT_WINDOW_CACHE", str(tmp_path / "cache"))
    env, _ = _pyannote_cli(tmp_path, exit_code=4 if failure == "rc4" else 1)
    binary = Path(env["SPEECHTOTEXT_DIARIZE_BIN"])
    if failure == "missing":
        binary.unlink()
    elif failure == "typo":
        env["SPEECHTOTEXT_DIARIZE_BACKEND"] = "typo"
    elif failure == "permission":
        binary.chmod(0o644)
    elif failure == "removed":
        resolved = stt_diarize.resolve_toolchain(env)
        assert resolved is not None
        binary.unlink()
        monkeypatch.setattr(stt_diarize, "resolve_toolchain", lambda _: resolved)
    result = _local_transcription(tmp_path, env)
    assert result.text == "합성 검증."
    assert result.sentences and all(sentence.speaker == "" for sentence in result.sentences)
    stderr = capsys.readouterr().err
    assert "DIARIZE-FAIL" in stderr
    if failure in {"rc1", "rc4"}:
        assert f"rc={failure[-1]}" in stderr
    if failure == "rc4":
        assert "STT-ENGINES-NO-TOKEN" in stderr


def test_diarize_candidates_match_environment_contract_and_matrix() -> None:
    import re

    path = REPO / "configs/stt-eval/candidates-diar.json"
    assert path.is_file()
    candidates = json.loads(path.read_text())
    assert len(candidates) == 10
    assert len({row["label"] for row in candidates}) == 10
    sources = (REPO / "skills/speechtotext/scripts").glob("stt*.py")
    allowed = {name for source in sources
               for name in re.findall(r"SPEECHTOTEXT_[A-Z0-9_]+", source.read_text())}
    matrix = set()
    for row in candidates:
        assert set(row) == {"label", "env_overrides"}
        env = row["env_overrides"]
        assert set(env) <= allowed
        assert all(isinstance(value, str) for value in env.values())
        assert all(not value.startswith("/") for value in env.values())
        backend = env["SPEECHTOTEXT_DIARIZE_BACKEND"]
        variant = (Path(env["SPEECHTOTEXT_DIARIZE_EMBEDDING"]).name if backend == "sherpa"
                   else env["SPEECHTOTEXT_DIARIZE_MODE"])
        if backend == "sherpa":
            assert env["SPEECHTOTEXT_DIARIZE_THRESHOLD"] == str(stt_diarize.DEFAULT_THRESHOLD)
        matrix.add((backend, variant, env["SPEECHTOTEXT_ALIGN_BACKEND"]))
    assert matrix == {
        (backend, variant, align)
        for backend, variants in (
            ("sherpa", ("3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
                        "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx",
                        "3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx")),
            ("pyannote", ("exclusive", "regular")),
        ) for variant in variants for align in ("none", "whisperx")
    }


def test_residual_clusters_are_not_speakers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """노드 실측(2026-09-07 · 임계값 1.0)의 군집 분포를 그대로 흉내 낸다.

    36.0/28.8/17.3/17.0% 네 사람 뒤에 0.7%·0.2% 두 조각이 붙어 나왔다. 발화 구조를
    되찾으려면 임계값을 낮춰야 하는데, 낮추면 이런 부스러기가 함께 생긴다. 그것까지
    화자로 세면 아무도 하지 않은 말에 화자5·화자6 이 붙는다.

    판정은 여기(분리기 경계)에 둔다 — stt_attribute 는 받은 turn 을 근거로 신뢰해야 하고,
    거기에 몫 바닥을 두면 1ms 겹침을 OVERLAP 으로 지키는 계약과 짧은 맞장구가 함께 죽는다.
    """
    env = _files(tmp_path)
    binary = Path(env["SPEECHTOTEXT_DIARIZE_BIN"])
    binary.write_text(
        "#!/bin/sh\n"
        "echo '0 -- 36 speaker_00'\n"
        "echo '36 -- 64.8 speaker_01'\n"
        "echo '64.8 -- 82.1 speaker_02'\n"
        "echo '82.1 -- 99.1 speaker_03'\n"
        "echo '99.1 -- 99.8 speaker_04'\n"
        "echo '99.8 -- 100.0 speaker_05'\n",
        encoding="utf-8",
    )
    toolchain = stt_diarize.resolve_toolchain(env)
    assert toolchain is not None

    turns = stt_diarize.diarize(tmp_path / "audio.wav", toolchain)

    assert sorted({turn.speaker for turn in turns}) == [0, 1, 2, 3]
    # 진단은 분리기가 실제로 낸 군집 수를 그대로 말한다 — 판정과 관측을 섞지 않는다.
    assert capsys.readouterr().err == "DIARIZE-CLUSTERS speakers=6 threshold=1.0 turns=6\n"


def test_every_cluster_below_the_floor_keeps_them_all() -> None:
    """전원을 지우면 라벨 0개가 된다 — 뭉뚱그린 화자보다 나쁘다(상한 보수와 같은 이유)."""
    turns = tuple(stt_diarize.Turn(index * 1_000, index * 1_000 + 1_000, index)
                  for index in range(25))

    assert stt_diarize.substantial(turns) == turns
