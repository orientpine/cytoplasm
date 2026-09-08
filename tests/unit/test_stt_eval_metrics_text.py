from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from itertools import product
import unittest

from automation.stt_eval.metrics_text import (
    EditCounts, EvalLimitError, cer, cer_rate, cpcer, cpcer_strict,
    silence_insertion, speaker_streams,
)
from automation.stt_eval.model import EvalRecord, EvalRecordError, EvalTurn, EvalWord, SpeakerTag


def _record(text: str = "", words: tuple[EvalWord, ...] = ()) -> EvalRecord:
    return EvalRecord("synthetic", "a" * 64, 1000, text, words, der_regions=((0, 1000),))


def _word(start: int, end: int, tag: SpeakerTag, time: int | None = 100) -> EvalWord:
    return EvalWord(str(start), start, end, time, None if time is None else time + 100,
                    tag, "missing" if time is None else "token")


A = SpeakerTag("SPEAKER", ("a",))
B = SpeakerTag("SPEAKER", ("b",))


class TextMetricsTests(unittest.TestCase):
    def test_cer_fixed_counts_and_exact_fractions(self) -> None:
        for hypothesis, counts in (
            ("가나다라", EditCounts(0, 0, 0, 4)),
            ("가나X라", EditCounts(1, 0, 0, 4)),
            ("가나X다라", EditCounts(0, 0, 1, 4)),
            ("가나라", EditCounts(0, 1, 0, 4)),
        ):
            with self.subTest(hypothesis=hypothesis):
                self.assertEqual(cer("가나다라", hypothesis), counts)
                rate = cer_rate(counts)
                self.assertIsInstance(rate, Fraction)
                self.assertEqual(rate, Fraction(counts.S + counts.D + counts.I, 4))

    def test_empty_and_normalized_empty_inputs(self) -> None:
        for text in ("", " \t\n", ".,!?"):
            with self.subTest(text=text):
                self.assertEqual(cer(text, ""), EditCounts(0, 0, 0, 0))
                self.assertIsNone(cer_rate(cer(text, "가")))
                self.assertEqual(cer(text, "가"), EditCounts(0, 0, 1, 0))
                self.assertEqual(cer("가", text), EditCounts(0, 1, 0, 1))
                self.assertIsNone(cpcer({"a": text}, {}))
                self.assertIsNone(cpcer_strict(_record(text), _record()))
        self.assertIsNone(cpcer({}, {}))
        self.assertIsNone(cpcer({}, {"a": "가"}))
        self.assertEqual(speaker_streams(_record()), {})
        self.assertEqual(silence_insertion(_record(), _record()), (0, 1000))

    def test_normalization_reuses_ko_cer_v1(self) -> None:
        self.assertEqual(cer_rate(cer("가 A, -3.5%.", "가a-3.5%")), Fraction(0))
        self.assertEqual(cer("1,000", "1000"), EditCounts(0, 1, 0, 5))
        self.assertEqual(cpcer({"a": "가 A!"}, {"b": "가a"}), Fraction(0))

    def test_backtrace_ties_are_deterministic(self) -> None:
        self.assertEqual(cer("ab", "ba"), EditCounts(2, 0, 0, 2))
        self.assertEqual(cer("aba", "bab"), EditCounts(0, 1, 1, 3))
        self.assertEqual(cer("aab", "aba"), EditCounts(2, 0, 0, 3))

    def test_counts_match_exhaustive_short_string_distance(self) -> None:
        strings = ["".join(chars) for n in range(4) for chars in product("ab", repeat=n)]
        for ref, hyp in product(strings, repeat=2):
            # 짧은 문자열의 가능한 편집 경로를 독립적인 재귀로 전수 비교한다.
            def distance(r: str, h: str) -> int:
                if not r or not h:
                    return len(r) + len(h)
                return min(distance(r[1:], h[1:]) + (r[0] != h[0]),
                           distance(r[1:], h) + 1, distance(r, h[1:]) + 1)
            counts = cer(ref, hyp)
            self.assertEqual(counts.S + counts.D + counts.I, distance(ref, hyp))
            self.assertEqual(len(ref) - counts.D + counts.I, len(hyp))

    def test_streams_sort_by_time_and_fall_back_to_character_order(self) -> None:
        record = _record("다 가 나 A!", (
            _word(0, 1, A, 300), _word(2, 3, A, 100),
            _word(4, 5, A, 200), _word(6, 8, B, None),
        ))
        self.assertEqual(speaker_streams(record), {"a": "가나다", "b": "a"})
        untimed = replace(record, words=tuple(replace(w, start_ms=None, end_ms=None)
                                              for w in reversed(record.words)))
        self.assertEqual(speaker_streams(untimed), {"a": "다가나", "b": "a"})

    def test_overlap_reference_has_one_actual_speaker_not_copied_candidates(self) -> None:
        record = _record("가나다", (
            _word(0, 1, A), _word(1, 2, SpeakerTag("OVERLAP", ("b",))),
            _word(2, 3, SpeakerTag("UNKNOWN")),
        ))
        self.assertEqual(speaker_streams(record), {"a": "가"})
        self.assertEqual(speaker_streams(record, reference=True), {"a": "가", "b": "나"})
        ambiguous = replace(record, words=(_word(0, 1, SpeakerTag("OVERLAP", ("a", "b"))),))
        self.assertEqual(speaker_streams(ambiguous), {})
        with self.assertRaises(EvalRecordError):
            _ = speaker_streams(ambiguous, reference=True)
        self.assertEqual(speaker_streams(_record("가", (_word(0, 1, SpeakerTag("SPEAKER", ("a", "b"))),))), {})

    def test_cpcer_actually_permutes_swapped_labels_and_pads(self) -> None:
        ref = {"a": "가나", "b": "다라"}
        hyp = {"a": "다라", "b": "가나"}
        self.assertEqual(sum(cer(ref[k], hyp[k]).S for k in ref), 4)
        self.assertEqual(cpcer(ref, hyp), Fraction(0))
        self.assertEqual(cpcer(ref, {"x": "가나"}), Fraction(1, 2))
        self.assertEqual(cpcer({"x": "가나"}, hyp), Fraction(1))
        self.assertEqual(cpcer(ref, {}), Fraction(1))
        self.assertEqual(cpcer(dict(reversed(tuple(ref.items()))), hyp), Fraction(0))

    def test_cpcer_eight_allowed_nine_rejected_on_either_side(self) -> None:
        self.assertEqual(cpcer({str(i): "가" for i in range(8)}, {}), Fraction(1))
        for ref, hyp in (({str(i): "" for i in range(9)}, {}),
                         ({}, {str(i): "" for i in range(9)})):
            with self.assertRaises(EvalLimitError):
                _ = cpcer(ref, hyp)

    def test_strict_adds_unknown_and_overlap_chars_to_base_numerator(self) -> None:
        ref = _record("가나다라", (_word(0, 2, A), _word(2, 4, B)))
        hyp = replace(ref, words=(_word(0, 2, B), _word(2, 4, A)))
        self.assertEqual(cpcer_strict(ref, hyp), Fraction(0))
        for tag in (SpeakerTag("UNKNOWN"), SpeakerTag("OVERLAP", ("a", "b"))):
            uncertain = replace(hyp, words=(replace(hyp.words[0], tag=tag), hyp.words[1]))
            base = cpcer(speaker_streams(ref, reference=True), speaker_streams(uncertain))
            strict = cpcer_strict(ref, uncertain)
            assert isinstance(base, Fraction) and isinstance(strict, Fraction)
            self.assertEqual(base, Fraction(1, 2))
            self.assertEqual(strict, Fraction(1))
            self.assertEqual(strict - base, Fraction(2, 4))
        punct = _record(".!", (_word(0, 2, SpeakerTag("UNKNOWN")),))
        self.assertEqual(cpcer_strict(ref, punct), Fraction(1))

    def test_silence_counts_three_chars_outside_reference_turns(self) -> None:
        ref = replace(_record(), turns=(EvalTurn(100, 500, "a"),))
        hyp = _record("가나다 A!", (_word(0, 3, A, 600), _word(4, 6, A, 200)))
        self.assertEqual(silence_insertion(hyp, ref), (3, 600))

    def test_silence_unions_regions_and_turns_without_double_counting(self) -> None:
        ref = replace(_record(), der_regions=((0, 800), (500, 1000)),
                      turns=(EvalTurn(200, 400, "a"), EvalTurn(300, 600, "b")))
        hyp = _record("가나다라", (_word(0, 1, A, 0), _word(1, 2, A, 500),
                               _word(2, 3, A, 600), _word(3, 4, A, None)))
        self.assertEqual(silence_insertion(hyp, ref), (2, 600))
        self.assertEqual(silence_insertion(hyp, replace(ref, der_regions=())), (0, 0))

    def test_silence_requires_whole_positive_word_interval_inside_scored_silence(self) -> None:
        ref = replace(_record(), turns=(EvalTurn(200, 400, "a"),), der_regions=((100, 800),))
        hyp = _record("가나다라마", (_word(0, 1, A, 150), _word(1, 2, A, 50),
            _word(2, 3, A, 750), _word(3, 4, A, 100), _word(4, 5, A, 400)))
        self.assertEqual(silence_insertion(hyp, ref), (2, 500))
        zero = replace(hyp, words=(replace(hyp.words[0], start_ms=500, end_ms=500),))
        self.assertEqual(silence_insertion(zero, ref), (0, 500))
