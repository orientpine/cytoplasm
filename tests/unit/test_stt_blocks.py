"""문장 한 줄 = 전사본의 단위. 블록·타이밍·화자가 그 줄을 감싼다.

The owner's complaint was concrete: a 94-minute transcript came back as 140 lines,
the longest 1,137 characters. Reading it required scrolling sideways. A sentence per
line is the fix, and whisper.cpp already knows when each sentence was said — the
timings were being thrown away on the way to the document.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Protocol

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "speechtotext"
sys.path.insert(0, str(SKILL / "scripts"))

import stt_blocks  # noqa: E402
import stt_polish  # noqa: E402
import stt_transcript  # noqa: E402


class _MonkeyPatch(Protocol):
    """환경을 바꾸는 pytest fixture의 필요한 표면만 적는다."""

    def setenv(self, name: str, value: str) -> None: ...

    def delenv(self, name: str, *, raising: bool = True) -> None: ...


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


def test_todo9_characterization_normalized_assembly_and_cached_document() -> None:
    """좌표를 추가하기 전 조립 시각과 저장된 문서 바이트를 고정한다."""
    words = (
        stt_blocks.TimedWord("  가\t", 100, 200),
        stt_blocks.TimedWord(" 나.\n", 300, 400),
        stt_blocks.TimedWord(" 다.", 500, 600),
    )
    sentences = stt_blocks.sentences_from_words(words)
    assert [(s.text, s.start_ms, s.end_ms) for s in sentences] == [
        ("가 나.", 100, 400), ("다.", 500, 600),
    ]
    cached = (
        "[00:00:01] 화자1\n가.\n나.\n\n"
        "<details><summary>cached</summary>\n\n가.  가.\n</details>\n\n"
        "[--:--:--] 화자2\n다."
    )
    assert stt_blocks.render(stt_blocks.group(stt_blocks.parse(cached))).encode() == cached.encode()


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
    assert [word.timing_source for word in words] == ["segment", "segment"]


def test_token_offsets_and_segment_inheritance_keep_distinct_timing_sources() -> None:
    """토큰 시각만 token 이고 세그먼트에서 물려받은 값은 segment 다."""
    words = stt_blocks.words_from_whisper([
        {
            "text": " 토큰 시각입니다.",
            "offsets": {"from": 1_000, "to": 2_000},
            "tokens": _tokens((" 토큰", 1_000, 1_500), (" 시각입니다.", 1_500, 2_000)),
        },
        {
            "text": " 상속 시각입니다.",
            "offsets": {"from": 2_000, "to": 3_000},
            "tokens": [{"text": " 상속 시각입니다."}],
        },
    ])

    assert [word.timing_source for word in words] == ["token", "token", "segment"]


def test_legacy_transcript_header_and_body_still_round_trip() -> None:
    """새 provenance 줄을 모르는 옛 문서도 본문 파스는 그대로다."""
    legacy = "# 옛 전사본\n\n- 원본 음성: old.m4a\n\n---\n\n[00:00:01] 화자1\n안녕하세요."
    header, body = stt_polish.split_document(legacy)
    polished = stt_polish.polish(body)

    document = stt_transcript.rewrite(header, polished, label="옛")

    assert stt_polish.split_document(document)[1] == polished.body + "\n"


def test_transcript_header_records_dtw_or_plain_offsets(monkeypatch: _MonkeyPatch) -> None:
    """새 전사본은 토큰 시각을 DTW preset 또는 기본 offsets 로 밝힌다."""
    class _Transcription:
        text = "안녕하세요."
        model = "local:test"

    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_DTW", "large-v3-turbo")
    dtw = stt_transcript.render(
        label="테스트", source_name="a.wav", transcription=_Transcription(),
        now=datetime(2026, 9, 7),
    )
    monkeypatch.delenv("SPEECHTOTEXT_WHISPER_DTW")
    offsets = stt_transcript.render(
        label="테스트", source_name="a.wav", transcription=_Transcription(),
        now=datetime(2026, 9, 7),
    )

    assert "- 토큰 시각: dtw:large-v3-turbo\n" in dtw
    assert "- 토큰 시각: offsets\n" in offsets


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


def test_whisper_tokens_are_concatenated_verbatim_inside_a_word() -> None:
    """whisper 토큰은 낱말 조각이다 — 사이에 공백을 넣으면 없는 띄어쓰기가 생긴다.

    이 테스트는 2026-09-06 이전 규칙("양쪽에 공백이 없으면 넣는다")을 뒤집는다. 그 규칙은
    whisper 가 내지 않는 픽스처(앞 공백 없는 *낱말* 둘)로 고정돼 있었고, 실제 입력인 토큰
    열에 걸리면 낱말을 조각냈다. 노드 실측(한국어 133초, 36 세그먼트): 그대로 이어 붙이면
    whisper 자신의 text 와 36/36 일치, 옛 규칙은 0/36 이었고 '통신 규약 같은 거' 가
    '통 신 규 약 같은 거' 로 나왔다.
    """
    segments = [
        {
            "text": " 통신 규약 같은 거.",
            "offsets": {"from": 0, "to": 2_000},
            "tokens": [
                {"text": " 통", "offsets": {"from": 0, "to": 300}},
                {"text": "신", "offsets": {"from": 300, "to": 600}},
                {"text": " 규", "offsets": {"from": 600, "to": 900}},
                {"text": "약", "offsets": {"from": 900, "to": 1_200}},
                {"text": " 같은", "offsets": {"from": 1_200, "to": 1_600}},
                {"text": " 거", "offsets": {"from": 1_600, "to": 1_900}},
                {"text": ".", "offsets": {"from": 1_900, "to": 2_000}},
            ],
        }
    ]

    words = stt_blocks.words_from_whisper(segments)

    assert stt_blocks.sentences_from_words(words)[0].text == "통신 규약 같은 거."


def test_a_word_split_across_tokens_keeps_its_trailing_punctuation() -> None:
    """실측 전사본의 '감사합니다 .' — 마침표 앞 공백이 조립 결함의 서명이었다."""
    segments = [
        {
            "text": " 감사합니다.",
            "offsets": {"from": 0, "to": 1_000},
            "tokens": [
                {"text": " 감사", "offsets": {"from": 0, "to": 400}},
                {"text": "합니다", "offsets": {"from": 400, "to": 800}},
                {"text": ".", "offsets": {"from": 800, "to": 1_000}},
            ],
        }
    ]

    words = stt_blocks.words_from_whisper(segments)

    assert stt_blocks.sentences_from_words(words)[0].text == "감사합니다."


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


def test_round_trip_preserves_bytes_when_body_contains_a_folded_block() -> None:
    # Given: whitespace and duplicate lines inside the HTML are opaque evidence.
    folded = (
        "<details><summary>repetition=0.18</summary>\n\n"
        "[00:14:45] 화자1 · 가명\n같은  문장입니다.\n같은  문장입니다.\n\n"
        "[00:15:00]\n다음 문장입니다.\n</details>"
    )
    body = f"앞 문장입니다.\n\n{folded}\n\n뒤 문장입니다."
    # When
    rendered = stt_blocks.render(stt_blocks.group(stt_blocks.parse(body)))
    # Then
    assert rendered == body
    assert stt_polish.polish(body).body == body


def test_polish_keeps_each_fold_when_folded_blocks_are_adjacent() -> None:
    # Given
    fold = "<details><summary>repetition=0.18</summary>\n같은 문장.\n</details>"
    body = f"{fold}\n\n{fold}"
    # When
    polished = stt_polish.polish(body)
    # Then
    assert polished.body == body


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


def test_polish_sentences_keeps_the_timings_and_the_words_it_was_given() -> None:
    """접기는 문장 단위로 시각을 지키고, 낱말은 들린 그대로 남는다(전사본은 증거다)."""
    sentences = (
        stt_blocks.TimedSentence("영무를 잡아놨습니다.", 1_000, 2_000, "화자1"),
        stt_blocks.TimedSentence("영무를 잡아놨습니다.", 2_000, 3_000, "화자1"),
        stt_blocks.TimedSentence("확인했습니다.", 3_000, 4_000, "화자1"),
    )
    result = stt_polish.polish_sentences(sentences)

    assert result.collapsed == 1
    assert result.sentences == 2
    assert result.timed[0].start_ms == 1_000
    assert result.body.splitlines()[0] == "[00:00:01] 화자1"
    assert "영무를 잡아놨습니다." in result.body


def test_todo9_seven_tokens_keep_normalized_spans_and_original_timing() -> None:
    words = tuple(
        stt_blocks.TimedWord(text, start, end)
        for text, start, end in (
            (" 통", 0, 300), ("신", 300, 600), (" 규", 600, 900),
            ("약", 900, 1_200), (" 같은", 1_200, 1_600),
            (" 거", 1_600, 1_900), (".", 1_900, 2_000),
        )
    )
    sentence = stt_blocks.sentences_from_words(words)[0]
    carried: tuple[stt_blocks.SentenceWord, ...] = getattr(sentence, "words", ())
    assert len(carried) == 7
    assert [(w.source_index, w.start_char, w.end_char) for w in carried] == [
        (0, 0, 1), (1, 1, 2), (2, 2, 4), (3, 4, 5),
        (4, 5, 8), (5, 8, 10), (6, 10, 11),
    ]
    assert tuple(w.word for w in carried) == words
    assert all(w.word is words[w.source_index] and not w.clipped for w in carried)
    assert stt_polish.polish_sentences((sentence,)).timed[0].words == carried


def test_todo9_cross_sentence_token_is_shared_without_interpolating() -> None:
    word = stt_blocks.TimedWord("요. 네.", 100, 900)
    sentences = stt_blocks.sentences_from_words((word,))
    assert [s.text for s in sentences] == ["요.", "네."]
    assert all(len(getattr(s, "words", ())) == 1 for s in sentences)
    for sentence in sentences:
        ref = sentence.words[0]
        assert (ref.source_index, ref.start_char, ref.end_char, ref.clipped) == (0, 0, 2, True)
        assert ref.word is word
        assert (sentence.start_ms, sentence.end_ms) == (100, 900)


def test_todo9_nfc_clusters_cross_token_boundaries_and_collapse_spaces() -> None:
    for fragments, normalized in (
        ((" ᄀ", "ᅡ", "ᆨ", "\t  나."), "각 나."),
        ((" e", "\u0301", "\t  나."), "é 나."),
        ((" a", "\u0315", "\u0300", "\t  나."), "à\u0315 나."),
    ):
        words = tuple(stt_blocks.TimedWord(text, i * 100, (i + 1) * 100)
                      for i, text in enumerate(fragments))
        sentence = stt_blocks.sentences_from_words(words)[0]
        assert sentence.text == normalized
        assert len(getattr(sentence, "words", ())) == len(words)
        cluster_end = normalized.index(" ")
        assert [(w.start_char, w.end_char) for w in sentence.words[:-1]] == [
            (0, cluster_end),
        ] * (len(words) - 1)
        assert (sentence.words[-1].start_char, sentence.words[-1].end_char) == (
            cluster_end, len(normalized),
        )
        assert tuple(w.word for w in sentence.words) == words
        assert not any(w.clipped for w in sentence.words)


def test_todo9_empty_missing_and_reversed_timings_are_not_fabricated() -> None:
    assert stt_blocks.sentences_from_words(stt_blocks.words_from_whisper([{}, {"tokens": []}])) == ()
    words = stt_blocks.words_from_whisper([
        {"tokens": [{"text": "가."}]},
        {"tokens": _tokens(("나.", 900, 100))},
    ])
    sentences = stt_blocks.sentences_from_words(words)
    assert all(len(getattr(s, "words", ())) == 1 for s in sentences)
    assert [(s.start_ms, s.end_ms) for s in sentences] == [(None, None), (900, 100)]
    assert [(s.words[0].source_index, s.words[0].word.start_ms,
             s.words[0].word.end_ms, s.words[0].word.timing_source) for s in sentences] == [
        (0, -1, -1, "segment"), (1, 900, 100, "token"),
    ]


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


def test_a_single_speaker_monologue_still_breaks_into_readable_blocks() -> None:
    """실측(2026-09-07): 화자 하나만 배정된 179문장 라이프로그가 블록 **하나**로 나왔다.

    화자가 바뀔 때만 블록을 닫으면 한 사람이 길게 말한 녹음은 벽 하나가 된다. 화자
    라벨이 붙었다는 사실은 문단 규칙을 끌 이유가 되지 못한다 — 사람이 읽는 단위는
    화자와 무관하게 그대로다.
    """
    sentences = tuple(
        stt_blocks.TimedSentence(
            f"{index}번째 문장입니다 이것은 사람이 읽기에 충분히 긴 문장입니다.",
            index * 3_000,
            index * 3_000 + 2_500,
            "화자1",
        )
        for index in range(20)
    )

    blocks = stt_blocks.group(sentences)

    assert len(blocks) >= 4
    assert {block.speaker for block in blocks} == {"화자1"}
    assert max(len(block.sentences) for block in blocks) <= 8
    starts = [block.start_ms for block in blocks]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(blocks)
