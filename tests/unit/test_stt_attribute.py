"""단어 배정의 정확한 경계와 직전 화자 비상속 계약."""

from __future__ import annotations

import importlib
import sys
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from skills.speechtotext.scripts import stt_attribute as attribute
    from skills.speechtotext.scripts.stt_diarize import Turn
    from skills.speechtotext.scripts.stt_sentence import SentenceWord as Ref
    from skills.speechtotext.scripts.stt_sentence import TimedWord as Word
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/speechtotext/scripts"))
    attribute = importlib.import_module("stt_attribute")
    sentence = importlib.import_module("stt_sentence")
    Word, Ref = sentence.TimedWord, sentence.SentenceWord
    Turn = importlib.import_module("stt_diarize").Turn


def assign(
    words: tuple[Word | Ref, ...], turns: tuple[Turn, ...], **overrides: int | Fraction,
) -> tuple[attribute.WordAttribution, ...]:
    return attribute.attribute_words(
        words, turns, policy=replace(attribute.AttributionPolicy(), **overrides),
    )


def test_short_backchannel_changes_speaker_without_minimum_duration() -> None:
    result = assign((Word("시작", 0, 500), Word(" 네", 1000, 1200)),
                    (Turn(0, 500, 8), Turn(1000, 1200, 2)))
    assert [item.tag.label for item in result] == ["화자1", "화자2"]
    assert result[1].support_ms == (("화자2", 200),)
    assert result[1].covered_ms == 200


@pytest.mark.parametrize("word, reason", [
    (Word(" 먼단어", 11000, 11200), "no_support"),
    (Word(" 미상", -1, -1), "timing"),
    (Word(" 영길이", 300, 300), "timing"),
    (Word(" 역전", 500, 200), "timing"),
    (Word(" 부분누락", -1, 200), "timing"),
    (replace(Word(" 없음", 0, 1), start_ms=None, end_ms=None), "timing"),
])
def test_no_previous_speaker_inheritance(word: Word, reason: str) -> None:
    turns = (Turn(0, 1000, 7),)
    alone = assign((word,), turns)[0]
    after_speaker = assign((Word("앞", 0, 200), word), turns)[1]
    assert alone.tag.kind == after_speaker.tag.kind == "UNKNOWN"
    assert alone.tag.label == after_speaker.tag.label == "화자0"
    assert alone.reason == after_speaker.reason == reason
    assert replace(after_speaker, source_index=0) == alone


@pytest.mark.parametrize("leading, kind, reason", [
    (115, "SPEAKER", "direct"),
    (114, "UNKNOWN", "boundary_tie"),
    (100, "UNKNOWN", "boundary_tie"),
])
def test_exact_fraction_margin(leading: int, kind: str, reason: str) -> None:
    result = assign((Word("경계", 0, 200),),
                    (Turn(0, leading, 0), Turn(leading, 200, 1)))[0]
    assert (result.tag.kind, result.reason) == (kind, reason)
    assert result.covered_ms == 200
    assert result.simultaneous_ms == 0


@pytest.mark.parametrize("end, kind", [(99, "UNKNOWN"), (100, "SPEAKER")])
def test_coverage_threshold(end: int, kind: str) -> None:
    result = assign((Word("부분", 0, 200),), (Turn(0, end, 3),))[0]
    assert result.tag.kind == kind
    assert result.reason == ("direct" if end == 100 else "low_coverage")


def test_simultaneous_includes_all_participants_and_counts_union_once() -> None:
    result = assign((Word("겹침", 0, 200),),
                    (Turn(0, 100, 4), Turn(50, 150, 5), Turn(100, 200, 6)))[0]
    assert result.tag.kind == "OVERLAP"
    assert result.tag.label == "화자0"
    assert result.tag.speakers == ("화자1", "화자2", "화자3")
    assert result.simultaneous_ms == 100
    assert result.covered_ms == 200
    assert result.support_ms == (("화자1", 100), ("화자2", 100), ("화자3", 100))


def test_tiny_simultaneous_overlap_precedes_dominant_coverage() -> None:
    result = assign((Word("중첩", 0, 1000),),
                    (Turn(0, 1000, 0), Turn(500, 501, 1)))[0]
    assert result.tag.kind == "OVERLAP"
    assert result.simultaneous_ms == 1


def test_same_speaker_turn_union_does_not_inflate_support_or_overlap() -> None:
    result = assign((Word("합집합", 0, 200),),
                    (Turn(0, 80, 3), Turn(20, 80, 3), Turn(0, 80, 3)))[0]
    assert result.support_ms == (("화자1", 80),)
    assert result.covered_ms == 80
    assert result.simultaneous_ms == 0
    assert result.reason == "low_coverage"


@pytest.mark.parametrize("gap, kind", [(0, "SPEAKER"), (200, "SPEAKER"), (201, "UNKNOWN")])
def test_nearest_interval_distance_and_inclusive_limit(gap: int, kind: str) -> None:
    result = assign((Word("근처", 10000 + gap, 10200 + gap),), (Turn(0, 10000, 9),))[0]
    assert result.tag.kind == kind
    assert result.nearest_ms == gap
    assert result.support_ms == ()
    assert result.reason == ("tolerance" if kind == "SPEAKER" else "no_support")


@pytest.mark.parametrize("difference, kind", [(0, "UNKNOWN"), (50, "UNKNOWN"), (51, "SPEAKER")])
def test_nearest_requires_strictly_more_than_tie_limit(difference: int, kind: str) -> None:
    result = assign((Word("근접경계", 1000, 1200),),
                    (Turn(0, 900, 0), Turn(1300 + difference, 2000, 1)))[0]
    assert result.tag.kind == kind
    assert result.reason == ("tolerance" if kind == "SPEAKER" else "boundary_tie")


def test_nearest_competes_between_speakers_not_turns() -> None:
    result = assign((Word("근처", 1000, 1200),),
                    (Turn(0, 900, 4), Turn(1300, 1400, 4)))[0]
    assert result.tag.kind == "SPEAKER"
    assert result.nearest_ms == 100


def test_labels_follow_all_turns_first_start_then_backend_id() -> None:
    turns = (Turn(0, 100, 9), Turn(0, 100, 2), Turn(500, 800, 9))
    refs = (Ref(8, 0, 1, Word("뒤", 500, 600)), Ref(3, 0, 1, Word(" 앞", 0, 100)))
    forward = assign(refs, turns)
    reverse = assign(tuple(reversed(refs)), tuple(reversed(turns)))
    assert forward == reverse
    assert [item.source_index for item in forward] == [3, 8]
    assert forward[1].tag.label == "화자2"


def test_empty_turns_and_empty_words_are_total() -> None:
    result = assign((Word("단어", 0, 100), Word(" 다음", 200, 300)), ())
    assert all(item.tag.kind == "UNKNOWN" and item.reason == "no_turns" for item in result)
    assert assign((), (Turn(0, 100, 0),)) == ()


def test_invalid_turns_are_not_evidence_or_labels() -> None:
    result = assign((Word("단어", 0, 100),),
                    (Turn(-1, 20, 8), Turn(200, 100, 7), Turn(0, 0, 4)))[0]
    assert (result.tag.kind, result.reason) == ("UNKNOWN", "no_turns")


def test_bpe_uses_valid_interval_union_not_envelope() -> None:
    words = (Word("맞", 0, 100), Word("아요", 900, 1000), Word("!", 100, 900))
    result = assign(words, (Turn(0, 1000, 8), Turn(100, 900, 9)))
    assert len(result) == 3
    assert all(item.tag.label == "화자1" for item in result)
    assert all(item.covered_ms == 200 and item.simultaneous_ms == 0 for item in result)
    assert all(item.support_ms == (("화자1", 200),) for item in result)


def test_bpe_overlapping_intervals_are_not_double_counted() -> None:
    result = assign((Word("맞", 0, 150), Word("아요", 50, 200)), (Turn(0, 100, 0),))
    assert all(item.tag.kind == "SPEAKER" and item.covered_ms == 100 for item in result)


def test_bpe_pieces_share_the_lexical_decision() -> None:
    result = assign((Word("맞", 0, 115), Word("아요", 115, 200)),
                    (Turn(0, 115, 0), Turn(115, 200, 1)))
    assert [item.tag.label for item in result] == ["화자1", "화자1"]


def test_punctuation_only_cannot_supply_speaker_evidence() -> None:
    result = assign((Word("...", 0, 100),), (Turn(0, 100, 4),))[0]
    assert result.tag.kind == "UNKNOWN"
    assert result.reason == "timing"
    assert result.support_ms == ()
    assert result.covered_ms == result.simultaneous_ms == 0


def test_whitespace_boundary_prevents_bpe_merging() -> None:
    result = assign((Word("맞 ", 0, 100), Word("네", 500, 600)),
                    (Turn(0, 100, 0), Turn(500, 600, 1)))
    assert [item.tag.label for item in result] == ["화자1", "화자2"]


def test_policy_is_exact_and_configurable() -> None:
    policy = attribute.AttributionPolicy()
    assert policy.min_coverage == Fraction(1, 2)
    assert policy.min_margin == Fraction(3, 20)
    assert (policy.tolerance_ms, policy.nearest_tie_ms) == (200, 50)
    result = assign((Word("정책", 0, 200),), (Turn(0, 80, 0),), min_coverage=Fraction(2, 5))
    assert result[0].tag.kind == "SPEAKER"


def test_no_prior_context_for_every_uncertain_scoring_branch() -> None:
    words = (Word(" 앞", 0, 100), Word(" 부족", 1000, 1200),
             Word(" 경계", 2000, 2200), Word(" 근접", 3000, 3200))
    turns = (Turn(0, 100, 0), Turn(1000, 1050, 0), Turn(2000, 2100, 0),
             Turn(2100, 2200, 1), Turn(2800, 2900, 0), Turn(3300, 3400, 1))
    result = assign(words, turns)
    assert result[0].tag.kind == "SPEAKER"
    for index, word in enumerate(words[1:], 1):
        alone = assign((word,), turns)[0]
        assert alone.tag.kind == "UNKNOWN"
        assert replace(result[index], source_index=0) == alone


def test_calls_do_not_retain_speaker_labels_or_mutate_inputs() -> None:
    words, turns = (Word("단어", 100, 200),), (Turn(100, 200, 99),)
    first = assign(words, turns)
    _ = assign((Word("다른", 0, 100),), (Turn(0, 100, 1), Turn(200, 300, 99)))
    assert assign(words, turns) == first
    assert words == (Word("단어", 100, 200),)
    assert turns == (Turn(100, 200, 99),)


def test_speaker_reason_breakdown_keeps_weak_speakers_visible_without_changing_assignment() -> None:
    # Given: two directly assigned speakers and two candidates that lose under existing rules.
    words = (Word("강함1", 0, 100), Word(" 강함2", 1000, 1100),
             Word(" 약함1", 2000, 2200), Word(" 약함2", 3301, 3501))
    turns = (Turn(0, 100, 10), Turn(1000, 1100, 11),
             Turn(2000, 2080, 12), Turn(3000, 3100, 13))

    # When: the existing attribution is measured by candidate speaker and reason.
    assigned = assign(words, turns)
    breakdown = attribute.speaker_reason_breakdown(assigned)

    # Then: weak candidates are counted at their losing rule and assignment is unchanged.
    assert [(item.tag.label, item.reason) for item in assigned] == [
        ("화자1", "direct"), ("화자2", "direct"),
        ("화자0", "low_coverage"), ("화자0", "no_support"),
    ]
    assert breakdown == (
        attribute.SpeakerReasonBreakdown("화자1", (attribute.SpeakerReasonCount("direct", 1),)),
        attribute.SpeakerReasonBreakdown("화자2", (attribute.SpeakerReasonCount("direct", 1),)),
        attribute.SpeakerReasonBreakdown("화자3", (attribute.SpeakerReasonCount("low_coverage", 1),)),
        attribute.SpeakerReasonBreakdown("화자4", (attribute.SpeakerReasonCount("no_support", 1),)),
    )
