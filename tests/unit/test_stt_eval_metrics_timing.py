"""T@200의 단어 대응·정수 경계·결측과 실제 보고 CLI를 검증한다."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import TypedDict, cast

import pytest

from automation.stt_eval import report_metrics
from automation.stt_eval.model import EvalRecord, EvalWord, SpeakerTag, dump_record


class _ScoredRecord(TypedDict):
    metrics: dict[str, float | None]


class _Report(TypedDict):
    micro: dict[str, float | None]
    records: list[_ScoredRecord]
    metric: str
    scored_n: int


def record(text: str = "가 나 다", *, shift: int | None = 0) -> EvalRecord:
    words = tuple(EvalWord(str(index), index * 2, index * 2 + 1,
                           None if shift is None else 500 + index * 1000 + shift,
                           None if shift is None else 900 + index * 1000 + shift,
                           SpeakerTag("SPEAKER", ("s",)),
                           "missing" if shift is None else "token") for index in range(3))
    return EvalRecord("synthetic", "a" * 64, 5000, text, words)


def timing(ref: EvalRecord, hyp: EvalRecord) -> Fraction | None:
    assert "t200" in report_metrics.METRICS
    from automation.stt_eval.metrics_timing import t200

    numerator, denominator = report_metrics.measure(ref, hyp, "t200")
    expected = Fraction(numerator, denominator) if denominator else None
    assert t200(ref, hyp) == expected
    return expected


@pytest.mark.parametrize(("shift", "expected"), [(0, 1), (200, 1), (-200, 1), (201, 0), (-201, 0)])
def test_t200_exact_and_integer_boundary(shift: int, expected: int) -> None:
    assert timing(record(), record(shift=shift)) == expected


def test_t200_no_reference_times_is_none_not_zero() -> None:
    assert timing(record(shift=None), record()) is None
    assert timing(replace(record(), words=()), record()) is None
    assert timing(record(), record(shift=None)) == 0


def test_t200_counts_words_not_characters_and_requires_both_boundaries() -> None:
    ref = record()
    hyp = replace(ref, words=(ref.words[0], replace(ref.words[1], end_ms=2101),
                              replace(ref.words[2], start_ms=None, end_ms=None, timing_source="missing")))
    assert timing(ref, hyp) == Fraction(1, 3)
    partial = replace(ref, words=(replace(ref.words[0], start_ms=None, end_ms=None,
                                          timing_source="missing"), *ref.words[1:]))
    assert timing(partial, hyp) == 0


def test_t200_reuses_text_diagonals_not_ids_order_or_nearest_time() -> None:
    ref = record()
    hyp = replace(ref, text="앞 가 나 다", words=(
        EvalWord("extra", 0, 1, 0, 100, SpeakerTag("UNKNOWN"), "token"),
        *(replace(word, id=f"new-{word.id}", char_start=word.char_start + 2,
                  char_end=word.char_end + 2) for word in reversed(ref.words))))
    assert timing(ref, hyp) == 1
    assert timing(ref, replace(record("가 X 다"), words=(ref.words[0], ref.words[2]))) == Fraction(2, 3)
    assert timing(ref, record("가 X 다")) == 1  # 치환도 기존 정렬의 대각 대응이다.
    swapped = replace(ref, words=(replace(ref.words[0], start_ms=2500, end_ms=2900),
                                 ref.words[1], replace(ref.words[2], start_ms=500, end_ms=900)))
    assert timing(ref, swapped) == Fraction(1, 3)


def test_t200_one_hypothesis_word_cannot_credit_two_reference_words() -> None:
    ref = record()
    ref = replace(ref, words=tuple(replace(word, start_ms=500, end_ms=900) for word in ref.words))
    hyp = replace(ref, words=(replace(ref.words[0], char_end=len(ref.text)),))
    assert timing(ref, hyp) == Fraction(1, 3)


def test_t200_cli_evaluate_compare_micro_and_null(tmp_path: Path) -> None:
    assert "t200" in report_metrics.METRICS
    ref = record()
    for directory in (tmp_path / "reference", *(tmp_path / "hyp" / (digit * 64) for digit in "de")):
        directory.mkdir(parents=True)
    for current in (ref, replace(ref, audio_sha256="b" * 64, words=ref.words[:1]),
                    replace(record(shift=None), audio_sha256="c" * 64)):
        dump_record(current, tmp_path / "reference" / f"{current.audio_sha256}.json")
        for config, words in (("d" * 64, current.words), ("e" * 64, ())):
            dump_record(replace(current, config_sha256=config, words=words),
                        tmp_path / "hyp" / config / f"{current.audio_sha256}.json")
    root = Path(__file__).resolve().parents[2]
    for args in (("evaluate", "--config", "d" * 64),
                 ("compare", "--a", "d" * 64, "--b", "e" * 64, "--metric", "t200", "--samples", "10")):
        completed = subprocess.run([sys.executable, "-m", "automation.stt_eval", *args,
                                    "--root", str(tmp_path), "--json"], cwd=root,
                                   capture_output=True, text=True, timeout=10, check=False)
        assert completed.returncode == 0, completed.stderr
        payload = cast(_Report, json.loads(completed.stdout))
        if args[0] == "evaluate":
            assert payload["micro"]["t200"] == 1
            assert payload["records"][2]["metrics"]["t200"] is None
        else:
            assert payload["metric"] == "t200"
            assert payload["scored_n"] == 2
            assert payload["micro"] == {"a": 1, "b": 0, "delta": -1}
    assert report_metrics.micro(((1, 3), (1, 1), (0, 0))) == Fraction(1, 2)
