"""문장이 화자보다 길면 화자 분리 결과가 문서에 도달하지 못한다.

현장 근거(2026-09): 4인이 말한 57초 중국어 샘플에서 whisper 가 구두점을 하나도 내지
않아 전사가 158자짜리 문장 **하나**로 나왔고, `assign` 은 그 한 덩어리를 통째로 화자1
에 붙였다. 화자 분리는 제대로 4명을 찾았는데도 문서에는 화자가 하나만 남은 것이다.
한국어 large-v3-turbo 출력에서도 735문장 중 1문장이 구두점 없이 나왔다.

그래서 `assign` 앞에 순수 분할 단계를 둔다. 이 파일은 그 분할이 (1) 화자 경계에서
끊고 (2) 구두점이 없으면 15초마다 끊고 (3) 근거가 없으면 아무것도 하지 않는다는 것을
고정한다.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "speechtotext"
sys.path.insert(0, str(SKILL / "scripts"))

import stt_blocks  # noqa: E402
import stt_diarize  # noqa: E402
import stt_split  # noqa: E402


@dataclass(frozen=True, slots=True)
class Sentence:
    """stt_blocks.TimedSentence 와 같은 모양의 오리 타입 — 분할기는 이것도 받는다."""

    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    speaker: str = ""


# 57초를 네 사람이 거의 균등하게 나눠 쓴 현장 샘플의 모양.
FOUR_TURNS = (
    stt_diarize.Turn(0, 14_000, 0),
    stt_diarize.Turn(14_000, 28_000, 1),
    stt_diarize.Turn(28_000, 42_000, 2),
    stt_diarize.Turn(42_000, 57_000, 3),
)


def _words(count: int) -> str:
    """초당 한 낱말로 말한 문장 — 글자 위치와 시각이 선형으로 대응한다."""
    return " ".join(f"낱말{index}" for index in range(count))


def test_four_speaker_sentence_splits_and_reaches_multiple_speakers() -> None:
    said = Sentence(_words(57), 0, 57_000)
    pieces = stt_split.split_on_turns((said,), FOUR_TURNS)
    assert len(pieces) >= 2
    assigned = stt_diarize.assign(pieces, FOUR_TURNS)
    assert len({sentence.speaker for sentence in assigned}) >= 2
    # 쪼갠 조각을 도로 이으면 원문이어야 한다. 분할은 말을 지우지 않는다.
    assert " ".join(piece.text for piece in pieces) == said.text


def test_punctuated_sentence_inside_one_turn_is_untouched() -> None:
    said = Sentence("안녕하세요 여러분 반갑습니다.", 1_000, 4_000)
    assert stt_split.split_on_turns((said,), (stt_diarize.Turn(0, 10_000, 0),)) == (said,)


def test_long_unpunctuated_sentence_inside_one_turn_splits() -> None:
    """한 화자만 말해도 40초짜리 무구두점 문장은 읽을 수 없다 — 15초마다 끊는다."""
    said = Sentence(_words(40), 0, 40_000)
    pieces = stt_split.split_on_turns((said,), (stt_diarize.Turn(0, 40_000, 0),))
    assert len(pieces) >= 2
    assert " ".join(piece.text for piece in pieces) == said.text


def test_untimed_sentences_pass_through() -> None:
    """시각이 없으면 자를 근거가 없다 — API 백엔드 문장은 그대로 지나간다."""
    said = (Sentence(_words(40)), Sentence("시간 없는 문장", None, 5_000))
    assert stt_split.split_on_turns(said, FOUR_TURNS) == said


def test_korean_text_splits_only_at_spaces() -> None:
    said = Sentence(_words(57), 0, 57_000)
    pieces = stt_split.split_on_turns((said,), FOUR_TURNS)
    rejoined: list[str] = []
    for piece in pieces:
        assert piece.text == piece.text.strip()
        rejoined.extend(piece.text.split())
    # 낱말이 반으로 잘리지 않았다면 조각의 낱말 목록은 원문의 낱말 목록과 같다.
    assert rejoined == said.text.split()


def test_sentence_without_word_boundaries_is_returned_whole() -> None:
    """띄어쓰기가 없으면 자를 자리가 없다 — 실패하지 말고 그대로 둔다."""
    said = Sentence("이것은전부붙어있는한덩어리라서자를자리가없다", 0, 57_000)
    assert stt_split.split_on_turns((said,), FOUR_TURNS) == (said,)


def test_pieces_keep_order_and_derive_timings_from_cut_points() -> None:
    said = Sentence(_words(57), 0, 57_000)
    pieces = stt_split.split_on_turns((said,), FOUR_TURNS)
    assert pieces[0].start_ms == said.start_ms
    assert pieces[-1].end_ms == said.end_ms
    for earlier, later in zip(pieces, pieces[1:]):
        assert earlier.start_ms < earlier.end_ms
        assert earlier.end_ms <= later.start_ms


def test_brief_turn_overlap_does_not_cut() -> None:
    """경계에 몇 백 ms 걸치는 것은 화자 교대가 아니라 분리기의 떨림이다."""
    said = Sentence(_words(4), 700, 3_500)
    turns = (stt_diarize.Turn(0, 1_000, 3), stt_diarize.Turn(3_000, 4_000, 1))
    assert stt_split.split_on_turns((said,), turns) == (said,)


def test_assign_splits_before_labelling_so_one_sentence_reaches_four_speakers() -> None:
    """현장에서 깨진 바로 그 경로: `assign` 하나만 불러도 화자가 살아남아야 한다.

    분할기를 만들어 두고 배정 앞에 끼우지 않으면 전사본은 그대로 화자1 하나로 남는다.
    """
    said = Sentence(_words(57), 0, 57_000)
    assigned = stt_diarize.assign((said,), FOUR_TURNS)
    assert len(assigned) >= 2
    assert len({sentence.speaker for sentence in assigned}) >= 2


def test_unpunctuated_chinese_segment_survives_the_real_assembly_path() -> None:
    """현장에서 깨진 그대로: 중국어는 띄어쓰기가 없지만 stt_blocks 가 토큰 사이에 공백을
    넣어 문장을 만든다. 그 공백이 곧 자를 자리이므로 CJK 도 화자별로 갈린다."""
    said = "我们今天要讨论的是这个项目的进度和预算安排以及下一步的计划" * 5
    tokens = [
        {"text": glyph, "offsets": {"from": index * 380, "to": (index + 1) * 380}}
        for index, glyph in enumerate(said)
    ]
    segments = [{"offsets": {"from": 0, "to": 57_000}, "text": said, "tokens": tokens}]
    sentences = stt_blocks.sentences_from_words(stt_blocks.words_from_whisper(segments))
    assert len(sentences) == 1  # 구두점이 없어 whisper 는 한 덩어리를 냈다.
    assigned = stt_diarize.assign(sentences, FOUR_TURNS)
    assert len({sentence.speaker for sentence in assigned}) >= 2


def test_speaker_cut_wins_over_punctuation() -> None:
    """구두점이 있어도 두 화자가 한 문장을 나눠 가졌다면 잘라야 한다."""
    said = Sentence(_words(30), 0, 30_000)
    turns = (stt_diarize.Turn(0, 15_000, 0), stt_diarize.Turn(15_000, 30_000, 1))
    punctuated = Sentence(said.text + ".", 0, 30_000)
    assert len(stt_split.split_on_turns((punctuated,), turns)) >= 2
