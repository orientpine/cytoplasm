"""문장 한 줄 = 전사본의 단위. 블록·타이밍·화자가 그 줄을 감싼다.

The owner's complaint was concrete: a 94-minute transcript came back as 140 lines,
the longest 1,137 characters. Reading it required scrolling sideways. A sentence per
line is the fix, and whisper.cpp already knows when each sentence was said — the
timings were being thrown away on the way to the document.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "speechtotext"
sys.path.insert(0, str(SKILL / "scripts"))

import stt_blocks  # noqa: E402
import stt_polish  # noqa: E402
import stt_transcript  # noqa: E402

_SAID = (
    "혹시 중앙중 교수님 이 계통 열수력 평가 이걸로 정리를 해도 좋을 것 같습니다. "
    "결과물들하고 제가 지금 생각하는 내용들을 연차별로 잡아봤고요. "
    "성능 요건하고 시험 요건을 검토하는 거가 1단계입니다. "
    "병행해서 유사 제품이나 기술들을 조사하고 분석해보는 거를 두 번째로 봤고. "
    "세 번째는 요건이 나오면 그거부터 개발을 실질적으로 하는 영무를 잡아놨습니다. "
    "설계 도서들은 27년 10월까지 데드라인을 목표로 하고 있습니다. "
    "예비 제작성 검토는 28년 4월로 잡혀 있습니다. "
    "고온고압 설비 구성은 28년 2월까지 결과물로 잡혀 있습니다."
)
_WALL = " ".join([_SAID] * 12)


def _tokens(*pairs: tuple[str, int, int]) -> list[dict[str, object]]:
    return [
        {"text": text, "offsets": {"from": start, "to": end}} for text, start, end in pairs
    ]


# --- whisper.cpp tokens -> words ---------------------------------------------


def test_special_tokens_never_reach_the_transcript() -> None:
    """`[_BEG_]`/`[_TT_520]` are decoder bookkeeping, not speech."""
    segments = [
        {
            "text": " 회의를 시작합니다.",
            "offsets": {"from": 0, "to": 2_000},
            "tokens": _tokens(
                ("[_BEG_]", 0, 0),
                (" 회의를", 0, 900),
                (" 시작합니다.", 900, 2_000),
                ("[_TT_520]", 2_000, 2_000),
            ),
        }
    ]
    words = stt_blocks.words_from_whisper(segments)
    assert [word.text for word in words] == [" 회의를", " 시작합니다."]
    assert words[0].start_ms == 0
    assert words[-1].end_ms == 2_000


def test_a_segment_without_usable_tokens_falls_back_to_its_own_offsets() -> None:
    """Some whisper builds emit segments with no token array — the text still counts."""
    segments = [
        {"text": " 다음 주까지 초안을 공유합니다.", "offsets": {"from": 3_000, "to": 6_500}},
        {"text": " 확인했습니다.", "offsets": {"from": 6_500, "to": 7_000}, "tokens": []},
    ]
    words = stt_blocks.words_from_whisper(segments)
    assert [word.text for word in words] == [
        " 다음 주까지 초안을 공유합니다.",
        " 확인했습니다.",
    ]
    assert (words[0].start_ms, words[0].end_ms) == (3_000, 6_500)
    assert (words[1].start_ms, words[1].end_ms) == (6_500, 7_000)


def test_sentences_span_the_words_that_make_them() -> None:
    words = (
        stt_blocks.TimedWord(" 회의를", 0, 900),
        stt_blocks.TimedWord(" 시작합니다.", 900, 2_000),
        stt_blocks.TimedWord(" 초안은", 2_400, 3_000),
        stt_blocks.TimedWord(" 다음 주입니다.", 3_000, 4_200),
    )
    sentences = stt_blocks.sentences_from_words(words)
    assert [sentence.text for sentence in sentences] == [
        "회의를 시작합니다.",
        "초안은 다음 주입니다.",
    ]
    assert (sentences[0].start_ms, sentences[0].end_ms) == (0, 2_000)
    assert (sentences[1].start_ms, sentences[1].end_ms) == (2_400, 4_200)


def test_word_boundaries_never_glue_two_words_together() -> None:
    """A space is inserted at a segment seam only when neither side already has one."""
    words = (
        stt_blocks.TimedWord("회의를", 0, 900),
        stt_blocks.TimedWord("시작합니다.", 900, 2_000),
    )
    assert stt_blocks.sentences_from_words(words)[0].text == "회의를 시작합니다."


def test_hhmmss_reads_as_a_clock() -> None:
    assert stt_blocks.hhmmss(0) == "00:00:00"
    assert stt_blocks.hhmmss(192_000) == "00:03:12"
    assert stt_blocks.hhmmss(3_723_000) == "01:02:03"


# --- one sentence per line, in blocks ----------------------------------------


def test_a_wall_of_text_becomes_one_sentence_per_line() -> None:
    """140 lines, the longest 1,137 characters — that document is what this replaces."""
    result = stt_polish.polish(_WALL)

    assert result.sentences == 96
    assert len(result.blocks) >= 4
    lines = [line for line in result.body.splitlines() if line.strip()]
    assert len(lines) == 96
    assert max(len(line) for line in lines) < 300


def test_blocks_carry_their_speaker_and_the_first_sentence_timing() -> None:
    sentences = (
        stt_blocks.TimedSentence("안녕하세요.", 192_000, 193_000, "화자1"),
        stt_blocks.TimedSentence("킥오프를 시작합니다.", 193_000, 195_000, "화자1"),
        stt_blocks.TimedSentence("네 좋습니다.", 195_500, 196_000, "화자2"),
    )
    blocks = stt_blocks.group(sentences)
    assert [block.speaker for block in blocks] == ["화자1", "화자2"]
    assert blocks[0].start_ms == 192_000
    assert blocks[0].sentences == ("안녕하세요.", "킥오프를 시작합니다.")

    rendered = stt_blocks.render(blocks, names={"화자1": "김민수"})
    assert rendered.splitlines()[0] == "[00:03:12] 화자1 · 김민수"
    assert rendered.splitlines()[1] == "안녕하세요."
    assert "[00:03:15] 화자2" in rendered


def test_render_and_parse_round_trip_through_the_document() -> None:
    sentences = (
        stt_blocks.TimedSentence("안녕하세요.", 192_000, 193_000, "화자1"),
        stt_blocks.TimedSentence("킥오프를 시작합니다.", 193_000, 195_000, "화자1"),
        stt_blocks.TimedSentence("네 좋습니다.", 195_500, 196_000, "화자2"),
    )
    body = stt_blocks.render(stt_blocks.group(sentences), names={"화자1": "김민수"})
    parsed = stt_blocks.parse(body)

    assert [sentence.text for sentence in parsed] == [
        "안녕하세요.",
        "킥오프를 시작합니다.",
        "네 좋습니다.",
    ]
    assert [sentence.speaker for sentence in parsed] == ["화자1", "화자1", "화자2"]
    assert parsed[0].start_ms == 192_000
    assert parsed[2].start_ms == 195_000
    # 이름은 헤더의 표시용이지 문장의 소유가 아니다 — 재렌더가 같은 문서를 낸다.
    assert stt_blocks.render(stt_blocks.group(parsed), names={"화자1": "김민수"}) == body


def test_a_block_without_timing_or_speaker_has_no_header() -> None:
    parsed = stt_blocks.parse("안녕하세요.\n킥오프를 시작합니다.")
    body = stt_blocks.render(stt_blocks.group(parsed))
    assert body.splitlines()[0] == "안녕하세요."
    assert "[--:--:--]" not in body


def test_a_speaker_without_timing_still_gets_a_header() -> None:
    blocks = stt_blocks.group((stt_blocks.TimedSentence("안녕하세요.", None, None, "화자2"),))
    assert stt_blocks.render(blocks).splitlines()[0] == "[--:--:--] 화자2"
    assert stt_blocks.parse("[--:--:--] 화자2\n안녕하세요.")[0].speaker == "화자2"


# --- the transcript already on disk ------------------------------------------


def test_a_legacy_paragraph_body_re_polishes_to_one_sentence_per_line() -> None:
    """The 94-minute transcript on disk is space-joined paragraphs; re-tidy repairs it."""
    legacy = "\n\n".join(stt_polish.paragraphs(stt_polish.split_sentences(_WALL)))
    legacy_lines = [line for line in legacy.splitlines() if line.strip()]
    assert len(legacy_lines) < 96  # 여러 문장이 한 줄에 눌려 있는 옛 문단

    result = stt_polish.polish(legacy)

    lines = [line for line in result.body.splitlines() if line.strip()]
    assert len(lines) == 96
    assert max(len(line) for line in lines) < 300
    for sentence in stt_polish.split_sentences(_SAID):
        assert sentence in lines


def test_polishing_an_already_polished_body_changes_nothing() -> None:
    once = stt_polish.polish(_WALL)
    twice = stt_polish.polish(once.body)
    assert twice.body == once.body
    assert twice.sentences == once.sentences
    assert twice.collapsed == 0


def test_polish_sentences_keeps_the_timings_it_was_given() -> None:
    sentences = (
        stt_blocks.TimedSentence("영무를 잡아놨습니다.", 1_000, 2_000, "화자1"),
        stt_blocks.TimedSentence("영무를 잡아놨습니다.", 2_000, 3_000, "화자1"),
        stt_blocks.TimedSentence("확인했습니다.", 3_000, 4_000, "화자1"),
    )
    result = stt_polish.polish_sentences(sentences, glossary=(("영무", "업무"),))

    assert result.substitutions == 2
    assert result.collapsed == 1
    assert result.sentences == 2
    assert result.timed[0].start_ms == 1_000
    assert result.body.splitlines()[0] == "[00:00:01] 화자1"
    assert "업무를 잡아놨습니다." in result.body


# --- the provenance header the blocks live under ------------------------------


def test_rewrite_replaces_managed_lines_and_appends_the_extra_ones() -> None:
    header = (
        "# 킥오프 전사본\n\n- 원본 음성: a.m4a\n"
        "- 다듬기: 문장 10개 · 문단 2개\n- 화자: 화자1=옛이름 [LLM]\n"
    )
    polished = stt_polish.polish(_SAID)
    document = stt_transcript.rewrite(
        header,
        polished,
        label="킥오프",
        extra_lines=("- 화자: 화자1=김민수 [자기소개 00:03:12] · 화자2=미상",),
        managed_prefixes=(stt_transcript.TIDY_PREFIX, "- 화자:"),
    )

    assert document.count("- 다듬기:") == 1
    assert document.count("- 화자:") == 1
    assert "화자1=김민수 [자기소개 00:03:12]" in document
    assert "- 원본 음성: a.m4a" in document
    body = stt_polish.split_document(document)[1]
    assert body.strip().splitlines()[0] == stt_polish.split_sentences(_SAID)[0]
