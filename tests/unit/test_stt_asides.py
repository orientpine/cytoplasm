"""문장 안 끼어듦(`[화자2: 네]`)이 문서 문법으로 왕복하고, 읽는 쪽이 표식을 말로 셈하지 않는다."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "speechtotext" / "scripts"))

import stt_asides  # noqa: E402
import stt_blocks  # noqa: E402
import stt_speaker_count  # noqa: E402
from stt_asides import Aside  # noqa: E402

from automation.stt_eval.cron import capture_text  # noqa: E402
from automation.stt_eval.model import SpeakerTag as EvalTag  # noqa: E402

HOST = stt_blocks.SpeakerTag("SPEAKER", ("화자1",))


def _sentence(text: str, *asides: Aside, start_ms: int | None = 5_000) -> stt_blocks.TimedSentence:
    return stt_blocks.TimedSentence(text, start_ms, None, "화자1", attribution=HOST, asides=asides)


def test_display_and_extract_are_inverse() -> None:
    text = "시작 네 계속."
    line = stt_asides.display(text, (Aside(3, 4, "화자2"),), {})
    assert line == "시작 [화자2: 네] 계속."
    assert stt_asides.extract(line) == (text, (Aside(3, 4, "화자2"),))


def test_name_is_shown_but_never_read_back() -> None:
    line = stt_asides.display("시작 네 계속.", (Aside(3, 4, "화자2"),), {"화자2": "이영희"})
    assert line == "시작 [화자2 · 이영희: 네] 계속."
    assert stt_asides.extract(line) == ("시작 네 계속.", (Aside(3, 4, "화자2"),))


def test_a_name_that_would_break_the_marker_is_left_out() -> None:
    line = stt_asides.display("시작 네 계속.", (Aside(3, 4, "화자2"),), {"화자2": "팀장: 김"})
    assert line == "시작 [화자2: 네] 계속."


def test_body_round_trips_with_asides_at_every_position() -> None:
    sentences = (
        _sentence("네 그렇게 하죠.", Aside(0, 1, "화자2")),
        _sentence("그 부분은 맞아요.", Aside(6, 10, "화자3"), start_ms=None),
        _sentence("다음 주에 봅시다 네.", Aside(10, 12, "화자2"), start_ms=None),
    )
    names = {"화자1": "김민수", "화자2": "이영희"}
    body = stt_blocks.render(stt_blocks.group(sentences), names)
    assert body.splitlines() == [
        "[00:00:05] 화자1 · 김민수",
        "[화자2 · 이영희: 네] 그렇게 하죠.",
        "그 부분은 [화자3: 맞아요.]",
        "다음 주에 봅시다 [화자2 · 이영희: 네.]",
    ]
    parsed = stt_blocks.parse(body)
    assert [(s.text, s.speaker, s.asides) for s in parsed] == [
        (sentence.text, "화자1", sentence.asides) for sentence in sentences
    ]
    assert stt_blocks.render(stt_blocks.group(parsed), names) == body
    assert stt_blocks.render(stt_blocks.group(parsed)) == stt_blocks.render(stt_blocks.group(sentences))


def test_a_line_that_starts_with_an_aside_is_not_a_header() -> None:
    assert stt_blocks.HEADER.match("[화자2: 네] 그렇죠.") is None
    parsed = stt_blocks.parse("[00:00:07] 화자1\n[화자2: 네] 그렇죠.")
    assert [(s.text, s.speaker, s.start_ms, s.asides) for s in parsed] == [
        ("네 그렇죠.", "화자1", 7_000, (Aside(0, 1, "화자2"),)),
    ]


def test_speaker_count_prompt_never_sees_aside_labels() -> None:
    draft = "[00:00:01] 화자1\n시작 [화자2 · 이영희: 네] 계속."
    assert stt_speaker_count.unlabelled(draft) == "[00:00:01]\n시작 네 계속."


def test_captured_reference_counts_aside_words_for_their_speaker() -> None:
    markdown = "- 화자: 화자1=김민수\n\n---\n\n[00:00:05] 화자1 · 김민수\n시작 [화자2: 네] 계속."
    parsed = capture_text.parse(markdown, "meeting")
    assert parsed.sentences == (
        ("시작", EvalTag("SPEAKER", ("화자1",))),
        ("네", EvalTag("SPEAKER", ("화자2",))),
        ("계속.", EvalTag("SPEAKER", ("화자1",))),
    )
    record = capture_text.build_record(parsed, digest="a" * 64, duration_ms=10_000,
                                       provenance="owner-edit:meeting:2026-09-22T00:00:00+00:00")
    assert record.text == "시작 네 계속."
