"""화자 경계에서 잘린 문장 조각을 근거가 허락하는 만큼만 되붙이는지 고정한다.

2026-09-22 실측: 13분 녹음의 71블록 중 32블록이 `화자0` 조각이었고 UNKNOWN 조각의 길이
중앙값은 4자였다(예: `…같이 넣어` / `봤어야 돼요`). 모든 합성 발화는 실녹음이 아니다.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "speechtotext" / "scripts"))

import stt_blocks  # noqa: E402
import stt_diarize  # noqa: E402
import stt_local_attribution  # noqa: E402
from stt_asides import Aside  # noqa: E402


def _words(*spoken: tuple[str, int, int]) -> tuple[stt_blocks.TimedWord, ...]:
    return tuple(stt_blocks.TimedWord(text, start, end) for text, start, end in spoken)


def _turns(*spans: tuple[int, int, int]) -> tuple[stt_diarize.Turn, ...]:
    return tuple(stt_diarize.Turn(start, end, speaker) for start, end, speaker in spans)


def _lines(sentences: tuple[stt_blocks.TimedSentence, ...]) -> list[tuple[str, str]]:
    return [(sentence.speaker, sentence.text) for sentence in sentences]


def test_short_unsupported_tail_rejoins_its_sentence() -> None:
    words = _words(("같이", 0, 1500), (" 넣어", 1500, 2900), (" 봤어야", 3000, 3500), (" 돼요.", 3500, 3900))
    turns = _turns((0, 3000, 0), (3000, 3200, 1))
    made = stt_local_attribution.sentences(words, turns)
    assert _lines(made) == [("화자1", "같이 넣어 봤어야 돼요.")]
    assert [ref.source_index for ref in made[0].words] == [0, 1, 2, 3]
    assert (made[0].start_ms, made[0].end_ms) == (0, 3900)


def test_leading_tie_fragment_joins_forward() -> None:
    words = _words(("그러니까", 0, 600), (" 우리가", 600, 2000), (" 해야죠.", 2000, 4000))
    turns = _turns((0, 300, 0), (300, 4000, 1))
    assert _lines(stt_local_attribution.sentences(words, turns)) == [("화자2", "그러니까 우리가 해야죠.")]


def test_tolerance_only_label_is_not_evidence_for_a_new_speaker() -> None:
    words = _words(("먼저", 0, 1900), (" 그거", 2150, 2500), (" 하고요.", 2800, 5000))
    turns = _turns((0, 2000, 0), (2000, 2100, 1), (2800, 5000, 0))
    made = stt_local_attribution.sentences(words, turns)
    assert _lines(made) == [("화자1", "먼저 그거 하고요.")]
    assert made[0].asides == ()


def test_firm_short_backchannel_stays_inside_the_sentence() -> None:
    words = _words(("시작", 0, 2000), (" 네", 2000, 2200), (" 계속.", 2200, 4000))
    turns = _turns((0, 2000, 0), (2000, 2200, 1), (2200, 4000, 0))
    made = stt_local_attribution.sentences(words, turns)
    assert _lines(made) == [("화자1", "시작 네 계속.")]
    assert made[0].asides == (Aside(3, 4, "화자2"),)
    body = stt_blocks.render(stt_blocks.group(made))
    assert body.splitlines() == ["[00:00:00] 화자1", "시작 [화자2: 네] 계속."]


def test_a_second_long_enough_to_be_a_turn_still_splits() -> None:
    words = _words(("시작", 0, 2000), (" 제가", 2000, 2700), (" 말할게요", 2700, 3500), (" 계속.", 3500, 6000))
    turns = _turns((0, 2000, 0), (2000, 3500, 1), (3500, 6000, 0))
    made = stt_local_attribution.sentences(words, turns)
    assert _lines(made) == [("화자1", "시작"), ("화자2", "제가 말할게요"), ("화자1", "계속.")]
    assert all(sentence.asides == () for sentence in made)


def test_long_unsupported_speech_is_not_claimed_by_the_host() -> None:
    words = _words(("시작", 0, 2000), (" 한참", 2600, 3500), (" 동안", 3500, 4500), (" 말했다.", 4500, 5000))
    turns = _turns((0, 2000, 0), (2000, 2300, 1))
    made = stt_local_attribution.sentences(words, turns)
    assert _lines(made) == [("화자1", "시작"), ("화자0", "한참 동안 말했다.")]
    assert made[1].attribution == stt_blocks.SpeakerTag("UNKNOWN")


def test_a_sentence_with_only_weak_pieces_comes_back_whole() -> None:
    words = _words(("음", 0, 300), (" 그래.", 300, 700))
    turns = _turns((0, 150, 0), (150, 300, 1))
    made = stt_local_attribution.sentences(words, turns)
    assert [sentence.text for sentence in made] == ["음 그래."]


def test_the_fifteen_second_cut_survives_settling() -> None:
    spoken = tuple((("" if index == 0 else " ") + f"낱말{index}", index * 1000, index * 1000 + 900)
                   for index in range(40))
    made = stt_local_attribution.sentences(_words(*spoken), _turns((0, 40_000, 0)))
    assert len(made) >= 3
    assert " ".join(sentence.text for sentence in made) == " ".join(f"낱말{i}" for i in range(40))
    assert {sentence.speaker for sentence in made} == {"화자1"}
