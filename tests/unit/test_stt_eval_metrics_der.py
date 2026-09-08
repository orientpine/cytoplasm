"""정수 구간과 녹음 전역 매핑으로 DER 계약을 검증한다."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from fractions import Fraction
from typing import cast
from unittest import TestCase

from automation.stt_eval.metrics_der import DerCounts, EvalLimitError, der, der_rate
from automation.stt_eval.model import EvalRecordError, EvalTurn


class TestDer(TestCase):
    def test_identical_turns(self) -> None:
        turns = (EvalTurn(0, 1301, "a"), EvalTurn(1301, 3000, "b"))
        counts = der(turns, turns, regions=((0, 3000),))
        self.assertEqual(counts, DerCounts(0, 0, 0, 3000))
        self.assertEqual(der_rate(counts), Fraction(0))

    def test_backchannel_and_collar(self) -> None:
        ref = (EvalTurn(0, 1400, "a"), EvalTurn(1400, 1600, "b"), EvalTurn(1600, 3000, "a"))
        hyp = (ref[0], ref[2])
        counts = der(ref, hyp, regions=((0, 3000),))
        collared = der(ref, hyp, regions=((0, 3000),), collar_ms=250)
        self.assertEqual(counts, DerCounts(200, 0, 0, 3000))
        self.assertEqual(der_rate(counts), Fraction(200, 3000))
        self.assertEqual(collared, DerCounts(0, 0, 0, 1800))
        self.assertEqual(der_rate(collared), Fraction(0))

    def test_swapped_labels(self) -> None:
        ref = (EvalTurn(0, 1000, "a"), EvalTurn(1000, 3000, "b"))
        hyp = (EvalTurn(0, 1000, "b"), EvalTurn(1000, 3000, "a"))
        self.assertEqual(der(ref, hyp, regions=((0, 3000),)), DerCounts(0, 0, 0, 3000))

    def test_overlap_is_speaker_time(self) -> None:
        ref = (EvalTurn(0, 2000, "a"), EvalTurn(1000, 3000, "b"))
        self.assertEqual(der(ref, ref, regions=((0, 3000),)), DerCounts(0, 0, 0, 4000))
        counts = der(ref, (EvalTurn(0, 3000, "x"),), regions=((0, 3000),))
        self.assertEqual(counts, DerCounts(1000, 0, 1000, 4000))
        self.assertEqual(der_rate(counts), Fraction(1, 2))

    def test_mapping_is_global_not_per_region(self) -> None:
        ref = (EvalTurn(0, 1000, "a"), EvalTurn(2000, 4000, "b"))
        hyp = (EvalTurn(0, 4000, "x"),)
        counts = der(ref, hyp, regions=((0, 1000), (2000, 4000)))
        self.assertEqual(counts, DerCounts(0, 0, 1000, 3000))

    def test_mapping_maximizes_time_not_number_of_intervals(self) -> None:
        ref = tuple(EvalTurn(i, i + 1, "a") for i in range(0, 10, 2)) + (EvalTurn(20, 120, "b"),)
        counts = der(ref, (EvalTurn(0, 120, "x"),), regions=((0, 120),))
        self.assertEqual(counts, DerCounts(0, 15, 5, 105))

    def test_more_hyp_speakers_uses_one_to_one_padding(self) -> None:
        ref = (EvalTurn(0, 1000, "a"),)
        hyp = (EvalTurn(0, 400, "x"), EvalTurn(400, 1000, "y"), EvalTurn(0, 1000, "z"))
        self.assertEqual(der(ref, hyp, regions=((0, 1000),)), DerCounts(0, 1000, 0, 1000))

    def test_duplicate_same_speaker_turns_are_a_set(self) -> None:
        ref = (EvalTurn(0, 1000, "a"), EvalTurn(500, 1500, "a"), EvalTurn(0, 1000, "a"))
        hyp = (EvalTurn(0, 1500, "x"), EvalTurn(500, 1000, "x"))
        self.assertEqual(der(ref, hyp, regions=((0, 1500),)), DerCounts(0, 0, 0, 1500))

    def test_regions_are_clipped_union_with_exact_boundaries(self) -> None:
        ref = (EvalTurn(0, 100, "a"),)
        hyp = (EvalTurn(4, 29, "x"),)
        counts = der(ref, hyp, regions=((25, 31), (3, 27), (3, 27), (80, 80)))
        self.assertEqual(counts, DerCounts(3, 0, 0, 28))

    def test_unscored_time_does_not_bias_mapping(self) -> None:
        ref = (EvalTurn(0, 10000, "a"), EvalTurn(10000, 10003, "b"))
        hyp = (EvalTurn(0, 10003, "x"),)
        self.assertEqual(der(ref, hyp, regions=((10000, 10003),)), DerCounts(0, 0, 0, 3))

    def test_collar_masks_false_alarms_on_both_sides(self) -> None:
        ref = (EvalTurn(1000, 2000, "a"),)
        hyp = (EvalTurn(750, 2250, "x"),)
        self.assertEqual(der(ref, hyp, regions=((0, 3000),)), DerCounts(0, 500, 0, 1000))
        self.assertEqual(der(ref, hyp, regions=((0, 3000),), collar_ms=250), DerCounts(0, 0, 0, 500))

    def test_collar_removes_time_before_global_mapping(self) -> None:
        ref = (EvalTurn(0, 200, "a"), EvalTurn(1000, 2000, "b"))
        hyp = (EvalTurn(0, 200, "x"), EvalTurn(1250, 1400, "x"), EvalTurn(1400, 1750, "y"))
        counts = der(ref, hyp, regions=((0, 2000),), collar_ms=250)
        self.assertEqual(counts, DerCounts(0, 0, 150, 500))

    def test_empty_inputs_preserve_false_alarm(self) -> None:
        self.assertEqual(der([], [], regions=((0, 100),)), DerCounts(0, 0, 0, 0))
        counts = der([], [EvalTurn(1, 8, "x")], regions=((0, 10),))
        self.assertEqual(counts, DerCounts(0, 7, 0, 0))
        self.assertIsNone(der_rate(counts))
        self.assertEqual(der([EvalTurn(1, 8, "a")], [], regions=((0, 10),)), DerCounts(7, 0, 0, 7))

    def test_no_regions_and_fully_collared_reference_are_unscored(self) -> None:
        turns = (EvalTurn(0, 200, "a"),)
        self.assertEqual(der(turns, turns, regions=()), DerCounts(0, 0, 0, 0))
        counts = der(turns, (), regions=((0, 200),), collar_ms=250)
        self.assertEqual(counts, DerCounts(0, 0, 0, 0))
        self.assertIsNone(der_rate(counts))

    def test_zero_length_and_touching_turns(self) -> None:
        ref = (EvalTurn(0, 10, "a"), EvalTurn(10, 20, "a"), EvalTurn(5, 5, "b"))
        self.assertEqual(der(ref, [EvalTurn(0, 20, "x")], regions=((0, 20),)), DerCounts(0, 0, 0, 20))

    def test_large_integer_times_do_not_round(self) -> None:
        start = 2**54 + 1
        ref = (EvalTurn(start, start + 5, "a"),)
        hyp = (EvalTurn(start + 1, start + 5, "x"),)
        counts = der(ref, hyp, regions=((start, start + 5),))
        self.assertEqual(counts, DerCounts(1, 0, 0, 5))
        self.assertEqual(der_rate(counts), Fraction(1, 5))

    def test_recalculation_order_and_input_immutability(self) -> None:
        ref = [EvalTurn(0, 20, "a"), EvalTurn(10, 30, "b")]
        hyp = [EvalTurn(0, 30, "x")]
        original = ref.copy(), hyp.copy()
        first = der(ref, hyp, regions=((0, 30),))
        self.assertEqual(first, der(ref, hyp, regions=((0, 30),)))
        self.assertEqual(first, der(ref[::-1], hyp, regions=((0, 30),)))
        self.assertEqual((ref, hyp), original)
        with self.assertRaises(FrozenInstanceError):
            setattr(first, "missed_ms", 99)
        self.assertFalse(hasattr(first, "__dict__"))

    def test_limit_eight_accepted_nine_ref_or_hyp_rejected(self) -> None:
        turns = tuple(EvalTurn(i, i + 1, str(i)) for i in range(9))
        self.assertEqual(der(turns[:8], turns[:8], regions=((0, 9),)), DerCounts(0, 0, 0, 8))
        for ref, hyp in ((turns, ()), ((), turns), (turns, turns)):
            with self.subTest(ref=len(ref), hyp=len(hyp)), self.assertRaises(EvalLimitError):
                _ = der(ref, hyp, regions=((0, 9),))

    def test_invalid_turn_boundaries_on_both_sides(self) -> None:
        for start, end in ((-1, 10), (10, 9), (0, -1), (True, 10), (0, Fraction(3, 2))):
            bad = (EvalTurn(cast(int, start), cast(int, end), "a"),)
            for ref, hyp in ((bad, ()), ((), bad)):
                with self.subTest(start=start, end=end, ref=bool(ref)), self.assertRaises(EvalRecordError):
                    _ = der(ref, hyp, regions=((0, 100),))

    def test_invalid_regions_and_collar(self) -> None:
        for start, end in ((-1, 10), (10, 9), (False, 10), (0, Fraction(1, 2))):
            with self.subTest(start=start, end=end), self.assertRaises(EvalRecordError):
                _ = der([], [], regions=((cast(int, start), cast(int, end)),))
        for collar in (-1, True, Fraction(1, 2)):
            with self.subTest(collar=collar), self.assertRaises(EvalRecordError):
                _ = der([], [], regions=(), collar_ms=cast(int, collar))
