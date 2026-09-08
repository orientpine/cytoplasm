"""가짜 실행 파일로 기본 전사의 argv·시각·문서 바이트를 특성화한다."""

from __future__ import annotations

import importlib
import json
import os
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias, cast

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/speechtotext/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if TYPE_CHECKING:
    from skills.speechtotext.scripts import stt_audio, stt_blocks, stt_client, stt_eval_build, stt_local, stt_polish, stt_transcript
    from skills.speechtotext.scripts import stt_diarize as diarize
    from skills.speechtotext.scripts import stt_local_attribution as attribution
else:
    stt_audio = importlib.import_module("stt_audio")
    stt_blocks = importlib.import_module("stt_blocks")
    stt_client = importlib.import_module("stt_client")
    stt_eval_build = importlib.import_module("stt_eval_build")
    stt_local = importlib.import_module("stt_local")
    stt_polish = importlib.import_module("stt_polish")
    stt_transcript = importlib.import_module("stt_transcript")
    diarize = importlib.import_module("stt_diarize")
    attribution = importlib.import_module("stt_local_attribution")

Pipeline: TypeAlias = tuple[stt_audio.CheckedAudio, stt_local.LocalToolchain, Path]

SEGMENTS = [{"text": "가나 7!", "offsets": {"from": 0, "to": 2000}, "tokens": [
    {"text": "가", "offsets": {"from": 0, "to": 500}},
    {"text": "나", "offsets": {"from": 500, "to": 1000}},
    {"text": " 7!", "offsets": {"from": 1000, "to": 2000}},
]}]


def executable(path: Path, source: str) -> Path:
    _ = path.write_text(f"#!{sys.executable}\n" + source, encoding="utf-8")
    path.chmod(0o700)
    return path


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Pipeline:
    for key in tuple(os.environ):
        if key.startswith(("SPEECHTOTEXT_", "STT_ENGINES_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    log = tmp_path / "asr-argv.json"
    ffmpeg = executable(tmp_path / "ffmpeg", "import sys, wave\n"
                        + "with wave.open(sys.argv[-1], 'wb') as w:\n"
                        + " w.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))\n"
                        + " w.writeframes(bytes(64000))\n")
    whisper = executable(tmp_path / "whisper", "import json, sys\nfrom pathlib import Path\n"
                         + "args = sys.argv[1:]\n"
                         + f"Path({str(log)!r}).write_text(json.dumps(args))\n"
                         + "target = args[args.index('-of') + 1] + '.json'\n"
                         + f"Path(target).write_text({json.dumps({'transcription': SEGMENTS}, ensure_ascii=False)!r})\n")
    model = tmp_path / "model.bin"
    _ = model.write_bytes(b"fixture")
    audio = tmp_path / "input.wav"
    _ = audio.write_bytes(b"fixture")
    chain = stt_local.LocalToolchain(whisper, model, ffmpeg, None, 1, "ko", 10.0,
                                    True, "", 0.8, "0", 30000, 0)
    return stt_audio.check_audio(audio), chain, log


def document(result: stt_transcript.TranscriptionLike) -> bytes:
    return stt_transcript.render(label="fixture", source_name="input.wav", transcription=result,
                                 now=datetime(2026, 1, 1)).encode()


def test_default_characterization(pipeline: Pipeline, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    audio, chain, log = pipeline
    marker = tmp_path / "align-called"
    binary = executable(tmp_path / "align", f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    monkeypatch.setenv("SPEECHTOTEXT_ALIGN_BIN", str(binary))
    unset = stt_local.transcribe(audio, chain)
    argv_unset = cast(list[str], json.loads(log.read_text()))
    monkeypatch.setenv("SPEECHTOTEXT_ALIGN_BACKEND", "none")
    explicit = stt_local.transcribe(audio, chain)
    argv_none = cast(list[str], json.loads(log.read_text()))
    assert not marker.exists()
    assert unset == explicit
    assert document(unset) == document(explicit)
    assert unset.text == "가나 7!"
    assert unset.sentences == stt_blocks.sentences_from_words(stt_blocks.words_from_whisper(SEGMENTS))
    for args in (argv_unset, argv_none):
        for flag in ("-f", "-of"):
            index = args.index(flag) + 1
            args[index] = Path(args[index]).name
    assert argv_unset == argv_none
    assert "-dtw" not in argv_unset
    assert "-ojf" in argv_unset


def timing_value(markdown: bytes) -> str:
    return next(line.split(":", 1)[1].strip() for line in markdown.decode().splitlines()
                if line.startswith(stt_transcript.TOKEN_TIMING_PREFIX))


@pytest.mark.parametrize("preset", ["", "large-v3-turbo"])
def test_legacy_header_and_document_parse_render_are_byte_identical(
    monkeypatch: pytest.MonkeyPatch, preset: str,
) -> None:
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_DTW", preset)
    monkeypatch.delenv("SPEECHTOTEXT_ALIGN_BACKEND", raising=False)
    body = "[00:00:01] 화자1\n가나다.\n\n[00:00:03] 화자2\n다음입니다."
    old = stt_client.Transcription(body, "local:fixture", "local")
    original = document(old)
    expected = f"dtw:{preset}" if preset else "offsets"
    assert timing_value(original) == expected
    for backend in ("none", "whisperx"):
        monkeypatch.setenv("SPEECHTOTEXT_ALIGN_BACKEND", backend)
        _, cached_body = stt_polish.split_document(original.decode())
        parsed = stt_blocks.parse(cached_body)
        rendered_body = stt_blocks.render(stt_blocks.group(parsed))
        rebuilt = document(replace(old, text=rendered_body, sentences=parsed))
        assert rebuilt == original
        assert timing_value(rebuilt) == expected


@pytest.mark.parametrize("preset", ["", "large-v3-turbo"])
def test_whisperx_pipeline_uses_aligned_sentence_words(
    pipeline: Pipeline, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, preset: str,
) -> None:
    from tests.unit.test_stt_align import fake_cli, output
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_DTW", preset)
    env, _ = fake_cli(tmp_path, output())
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    audio, chain, _ = pipeline
    result = stt_local.transcribe(audio, chain)
    assert [(r.word.start_ms, r.word.end_ms, r.word.timing_source)
            for r in result.sentences[0].words] == [
                (100, 200, "aligned"), (300, 400, "aligned"), (1000, 2000, "token")]
    assert timing_value(document(result)) == "aligned:whisperx"
    monkeypatch.setenv("SPEECHTOTEXT_ALIGN_BACKEND", "none")
    assert timing_value(document(result)) == "aligned:whisperx"


@pytest.mark.parametrize("preset", ["", "large-v3-turbo"])
@pytest.mark.parametrize("failure", ["missing", "nonzero", "null"])
def test_alignment_failure_preserves_transcription(
    pipeline: Pipeline, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str], failure: str, preset: str,
) -> None:
    from tests.unit.test_stt_align import fake_cli, output
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_DTW", preset)
    audio, chain, _ = pipeline
    baseline = stt_local.transcribe(audio, chain)
    baseline_document = document(baseline)
    _ = capsys.readouterr()
    if failure == "nonzero":
        env, _ = fake_cli(tmp_path, output(), rc=4)
    elif failure == "null":
        rows = [{"text": row["text"], "start_ms": None, "end_ms": None, "score": None}
                for row in output()]
        env, _ = fake_cli(tmp_path, rows)
    else:
        env = {"SPEECHTOTEXT_ALIGN_BACKEND": "whisperx", "STT_ENGINES_VENV": str(tmp_path / "missing")}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    result = stt_local.transcribe(audio, chain)
    # 평가 지문은 정렬 요청 설정이 달라졌음을 기록하고, 그 외 결과는 그대로다.
    assert isinstance(result, stt_eval_build.SnapshotTranscription)
    assert isinstance(baseline, stt_eval_build.SnapshotTranscription)
    assert result.eval_record is not None and baseline.eval_record is not None
    assert result.eval_record.config_sha256 != baseline.eval_record.config_sha256
    normalized = replace(result.eval_record, config_sha256=baseline.eval_record.config_sha256)
    assert replace(result, eval_record=normalized) == baseline
    assert document(result) == baseline_document
    assert timing_value(document(result)) == (f"dtw:{preset}" if preset else "offsets")
    expected = [] if failure == "null" else [
        "ALIGN-FAIL rc=4" if failure == "nonzero" else "ALIGN-FAIL FileNotFoundError"]
    assert [line for line in capsys.readouterr().err.splitlines() if line.startswith("ALIGN-FAIL")] == expected


def test_all_aligned_words_keep_word_attribution() -> None:
    words = (stt_blocks.TimedWord("가", 0, 500), stt_blocks.TimedWord(" 나", 1000, 1500))
    turns = (diarize.Turn(0, 1500, 0), diarize.Turn(0, 1500, 1))
    baseline = attribution.sentences(words, turns)
    aligned = attribution.sentences(tuple(replace(word, timing_source="aligned") for word in words), turns)
    assert [(s.text, s.speaker) for s in baseline] == [("가 나", "화자0")]
    assert baseline[0].attribution is not None
    assert baseline[0].attribution.kind == "OVERLAP"
    assert [(s.text, s.speaker) for s in aligned] == [(s.text, s.speaker) for s in baseline]
    assert [s.attribution for s in aligned] == [s.attribution for s in baseline]
    assert [ref.word.timing_source for s in aligned for ref in s.words] == ["aligned", "aligned"]
