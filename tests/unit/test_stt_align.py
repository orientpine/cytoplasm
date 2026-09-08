"""격리 정렬 CLI의 문자 좌표·폴백·오류 경계를 확인한다."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/speechtotext/scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
stt_align = importlib.import_module("stt_align")
stt_blocks = importlib.import_module("stt_blocks")


@pytest.fixture
def words():
    return (stt_blocks.TimedWord("가", 0, 500), stt_blocks.TimedWord("나", 500, 1000),
            stt_blocks.TimedWord(" 7!", 1000, 2000))


def fake_cli(tmp_path: Path, rows: object, *, rc: int = 0) -> tuple[dict[str, str], Path]:
    binary = tmp_path / "venv/bin/stt-engines"
    binary.parent.mkdir(parents=True, exist_ok=True)
    record = tmp_path / "align-request.json"
    binary.write_text(f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n"
                      "assert sys.argv[1:3] == ['align', '--wav']\n"
                      "assert sys.argv[4] == '--segments'\n"
                      "path = Path(sys.argv[5])\n"
                      f"Path({str(record)!r}).write_text(json.dumps({{'argv': sys.argv[1:], 'segments': json.loads(path.read_text())}}))\n"
                      f"print({json.dumps(rows, ensure_ascii=False)!r})\n"
                      f"sys.exit({rc})\n", encoding="utf-8")
    binary.chmod(0o700)
    return {"SPEECHTOTEXT_ALIGN_BACKEND": "whisperx", "STT_ENGINES_VENV": str(binary.parents[1])}, record


def output(text: str = "가나 7!") -> list[dict[str, str | int | float | None]]:
    return [{"text": char, "start_ms": start, "end_ms": end, "score": score}
            for char, start, end, score in zip(text, (100, 300, None, None, None),
                                             (200, 400, None, None, None),
                                             (0.9, 0.8, None, None, None), strict=True)]


@pytest.mark.parametrize("env", [{}, {"SPEECHTOTEXT_ALIGN_BACKEND": "none", "SPEECHTOTEXT_ALIGN_BIN": "/missing"}])
def test_none_has_no_effects(words, monkeypatch, tmp_path, env):
    def forbidden(*args, **kwargs):
        pytest.fail("비활성 백엔드의 subprocess 호출")
    monkeypatch.setattr(subprocess, "run", forbidden)
    assert stt_align.align_words(tmp_path / "absent.wav", words, env=env) is words
    assert list(tmp_path.iterdir()) == []


def test_character_alignment_updates_words_and_sentence_refs(tmp_path, words):
    env, record = fake_cli(tmp_path, output())
    result = stt_align.align_words(tmp_path / "audio.wav", words, env=env)
    assert [(word.start_ms, word.end_ms, word.timing_source) for word in result] == [
        (100, 200, "aligned"), (300, 400, "aligned"), (1000, 2000, "token")]
    assert result[2] is words[2]
    assert [word.text for word in result] == [word.text for word in words]
    refs = stt_blocks.sentences_from_words(result)[0].words
    assert tuple(ref.word for ref in refs) == result
    assert [ref.source_index for ref in refs] == [0, 1, 2]
    request = json.loads(record.read_text())
    assert request["segments"] == [{"text": "가나 7!", "start": 0.0, "end": 2.0}]
    assert not Path(request["argv"][-1]).exists()


@pytest.mark.parametrize("rows", [None, {}, [], [{"text": "다", "start_ms": 0, "end_ms": 1}],
    [{"text": "가나 7!", "start_ms": 0, "end_ms": 1, "score": 0.9}]])
def test_invalid_output_is_atomic_fallback(tmp_path, words, capsys, rows):
    env, _ = fake_cli(tmp_path, rows)
    assert stt_align.align_words(tmp_path / "audio.wav", words, env=env) is words
    assert capsys.readouterr().err.splitlines() == ["ALIGN-FAIL invalid-output"]


@pytest.mark.parametrize("field,value", [("start_ms", -1), ("start_ms", True), ("start_ms", 0.5),
    ("end_ms", 99), ("end_ms", 2001), ("start_ms", None), ("score", 2), ("score", float("nan"))])
def test_invalid_times_or_score_fall_back(tmp_path, words, capsys, field, value):
    rows = output()
    rows[0][field] = value
    env, _ = fake_cli(tmp_path, rows)
    assert stt_align.align_words(tmp_path / "audio.wav", words, env=env) is words
    assert capsys.readouterr().err.splitlines() == ["ALIGN-FAIL invalid-output"]


def test_nonzero_exit_preserves_tokens_and_cleans_input(tmp_path, words, capsys):
    env, record = fake_cli(tmp_path, output(), rc=4)
    assert stt_align.align_words(tmp_path / "audio.wav", words, env=env) is words
    assert capsys.readouterr().err.splitlines() == ["ALIGN-FAIL rc=4"]
    assert not Path(json.loads(record.read_text())["argv"][-1]).exists()


def test_missing_venv_is_fail_soft(tmp_path, words, capsys):
    env = {"SPEECHTOTEXT_ALIGN_BACKEND": "whisperx", "STT_ENGINES_VENV": str(tmp_path / "absent")}
    assert stt_align.align_words(tmp_path / "audio.wav", words, env=env) is words
    assert capsys.readouterr().err.splitlines() == ["ALIGN-FAIL FileNotFoundError"]


def test_timeout_is_fail_soft_without_waiting(tmp_path, words, monkeypatch, capsys):
    env, _ = fake_cli(tmp_path, output())
    def timeout(argv, **kwargs):
        assert kwargs["timeout"] > 0
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", timeout)
    assert stt_align.align_words(tmp_path / "audio.wav", words, env=env) is words
    assert capsys.readouterr().err.splitlines() == ["ALIGN-FAIL TimeoutExpired"]


def test_split_token_and_nfc_use_sentence_coordinates(tmp_path):
    words = (stt_blocks.TimedWord("e", 0, 500), stt_blocks.TimedWord("́. 나.", 500, 2000))
    rows = [{"text": c, "start_ms": n * 100, "end_ms": n * 100 + 50, "score": 0.9}
            for n, c in enumerate("é.나.")]
    env, record = fake_cli(tmp_path, rows)
    result = stt_align.align_words(tmp_path / "audio.wav", words, env=env)
    assert [(w.start_ms, w.end_ms, w.timing_source) for w in result] == [(0, 50, "aligned"), (0, 350, "aligned")]
    assert [r["text"] for r in json.loads(record.read_text())["segments"]] == ["é.", "나."]


def test_partial_oov_token_keeps_whole_original_span(tmp_path):
    words = (stt_blocks.TimedWord("가나 7!", 0, 2000, timing_source="segment"),)
    env, _ = fake_cli(tmp_path, output())
    assert stt_align.align_words(tmp_path / "audio.wav", words, env=env) == words


def test_no_usable_sentences_never_calls_cli(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("정렬할 문장이 없는 subprocess 호출")
    monkeypatch.setattr(subprocess, "run", forbidden)
    words = (stt_blocks.TimedWord("가", -1, -1),)
    assert stt_align.align_words(tmp_path / "audio.wav", words,
                                 env={"SPEECHTOTEXT_ALIGN_BACKEND": "whisperx"}) is words
