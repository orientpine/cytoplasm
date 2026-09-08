"""미상·겹침 문서의 직렬화와 실제 이름 소비자 경계를 검증한다."""

from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from automation.plaud_sync import lifelog_fields

ROOT = Path(__file__).resolve().parents[2]
for skill in ("speechtotext", "meeting"):
    sys.path.insert(0, str(ROOT / "skills" / skill / "scripts"))
blocks = importlib.import_module("stt_blocks")
speakers = importlib.import_module("stt_speakers")
attribute = importlib.import_module("stt_attribute")
meeting = importlib.import_module("meeting_schema")
minutes = importlib.import_module("meeting_minutes")
flow = importlib.import_module("stt_speaker_flow")


def test_todo11_characterization_legacy_document() -> None:
    """이름 접미 해석과 블록 첫 시각만 남기는 기존 동작을 고정한다."""
    body = "[00:00:12] 화자1\n가.\n나.\n\n[--:--:--] 화자2\n다."
    parsed = blocks.parse(body)
    assert [(s.text, s.start_ms, s.end_ms, s.speaker, s.folded, s.words) for s in parsed] == [
        ("가.", 12000, None, "화자1", "", ()),
        ("나.", None, None, "화자1", "", ()),
        ("다.", None, None, "화자2", "", ()),
    ]
    assert blocks.render(blocks.group(parsed)).encode() == body.encode()
    named = "[00:00:12] 화자1 · 가명 · 부서\n가."
    assert blocks.parse(named) == blocks.parse("[00:00:12] 화자1\n가.")
    assert blocks.render(blocks.group(blocks.parse(named)), {"화자1": "가명 · 부서"}) == named


def test_todo11_characterization_real_speaker_consumers() -> None:
    assert lifelog_fields.speaker_count("[00:00 · 화자1] 가.\n[00:01 · 화자1] 나.\n[00:02 · Speaker 2] 다.") == 2
    inferred = speakers.infer((blocks.TimedSentence("저는 가나입니다.", speaker="화자1"),))
    assert speakers.names(inferred) == {"화자1": "가나"}
    assert speakers.parse_legend(speakers.render_legend(inferred)) == inferred
    parsed = meeting.parse_extraction(json.dumps({"speakers": [{"label": "화자1"}]}))
    assert parsed.speakers == (meeting.SpeakerRef("화자1"),)


def test_attribution_render_parse_and_group_preserve_structure() -> None:
    assert "attribution" in blocks.TimedSentence.__dataclass_fields__
    tags = (
        attribute.SpeakerTag("UNKNOWN"),
        attribute.SpeakerTag("OVERLAP", ("화자1", "화자2")),
        attribute.SpeakerTag("OVERLAP", ("화자1", "화자3")),
    )
    sentences = tuple(
        blocks.TimedSentence("가.", i * 1000, speaker="화자0", attribution=tag)
        for i, tag in enumerate(tags)
    )
    grouped = blocks.group(sentences)
    assert len(grouped) == 3
    assert [b.attribution for b in grouped] == list(tags)
    body = blocks.render(grouped, {"화자0": "잘못된 이름 · 부서"})
    assert [line for line in body.splitlines() if line.startswith("[")] == [
        "[00:00:00] 화자0 · UNKNOWN",
        "[00:00:01] 화자0 · OVERLAP(화자1,화자2)",
        "[00:00:02] 화자0 · OVERLAP(화자1,화자3)",
    ]
    assert blocks.parse(body) == sentences
    assert blocks.group(blocks.parse(body)) == grouped
    assert len(blocks.group((sentences[0], sentences[0]))) == 1


def test_group_uses_tag_state_and_speakers_for_confirmed_speech() -> None:
    assert "attribution" in blocks.TimedSentence.__dataclass_fields__
    tagged = blocks.TimedSentence("가.", speaker="화자1", attribution=attribute.SpeakerTag("SPEAKER", ("화자1",)))
    legacy = blocks.TimedSentence("나.", speaker="화자1")
    assert len(blocks.group((tagged, legacy))) == 1
    assert blocks.parse("[00:00:12] 화자1 · UNKNOWN\n가.")[0].attribution is None


@pytest.mark.parametrize("suffix", ["", " · OVERLAP(", " · OVERLAP()", " · OVERLAP(화자1)",
                                      " · OVERLAP(화자0,화자1)", " · OVERLAP(화자1,화자1)",
                                      " · OVERLAP(화자1,이름 · 부서)", " · 이름 · 부서"])
def test_broken_unknown_suffix_is_not_a_name_or_an_exception(suffix: str) -> None:
    parsed = blocks.parse(f"[00:00:12] 화자0{suffix}\n가.")
    assert parsed[0].speaker == "화자0"
    assert getattr(parsed[0], "attribution", "missing") is None
    assert blocks.render(blocks.group(parsed), {"화자0": "UNKNOWN"}) == "[00:00:12] 화자0\n가."


def test_gap_marker_never_inherits_speaker_or_attribution() -> None:
    marker = blocks.stt_gap.marker(12000, 13000)
    parsed = blocks.parse(f"[00:00:12] 화자0 · UNKNOWN\n{marker}")
    assert parsed[0].speaker == ""
    assert getattr(parsed[0], "attribution", "missing") is None
    assert "attribution" in blocks.TimedSentence.__dataclass_fields__
    dirty = blocks.TimedSentence(marker, 12000, speaker="화자0", attribution=attribute.SpeakerTag("UNKNOWN"))
    grouped = blocks.group((dirty,))
    assert (grouped[0].speaker, grouped[0].attribution) == ("", None)


def test_sentence_factories_return_attribution_capable_public_type() -> None:
    for sentence in (*blocks.sentences_from_words((blocks.TimedWord("가.", 0, 1000),)),
                     *blocks.sentences_from_text("나.")):
        assert isinstance(sentence, blocks.TimedSentence)
        assert getattr(sentence, "attribution", "missing") is None


def test_all_name_boundaries_exclude_zero_even_with_a_plausible_introduction() -> None:
    inferred = speakers.infer((blocks.TimedSentence("저는 가나입니다.", speaker="화자0"),
                               blocks.TimedSentence("저는 가나입니다.", speaker="화자1")))
    assert speakers.names(inferred) == {"화자1": "가나"}
    zero = (speakers.SpeakerName("화자0", "UNKNOWN", "소유자"),)
    assert speakers.merge(zero) == ()
    assert speakers.names(zero) == {}
    assert speakers.render_legend(zero) == ""
    assert speakers.parse_override("화자0=UNKNOWN") == ()
    assert speakers.parse_llm(({"label": "화자0", "name": "UNKNOWN"},)) == ()
    assert speakers.parse_legend("- 화자: 화자0=UNKNOWN [LLM]") == ()
    combined = speakers.merge(zero, inferred)
    assert len(flow.speaker_summary(combined)) == 1
    legend = speakers.render_legend(zero + inferred)
    assert speakers.parse_legend(legend) == inferred
    assert "화자0" not in legend


@pytest.mark.parametrize("body, count", [
    ("[00:00 · 화자0] 가.", 0),
    ("[00:00 · 화자0] 가.\n[00:01 · 화자1] 나.", 1),
    ("[00:00:00] 화자0 · UNKNOWN\n가.\n\n[00:00:01] 화자0 · OVERLAP(화자1,화자2)\n나.", 0),
    ("[00:00:00] 화자0 · UNKNOWN\n가.\n\n[00:00:01] 화자1 · 이름 · 부서\n나.\n\n[00:00:02] 화자1\n다.", 1),
])
def test_lifelog_counts_actual_labels_not_unknown_or_overlap_participants(body: str, count: int) -> None:
    assert lifelog_fields.speaker_count(body) == count


def test_meeting_schema_and_minutes_exclude_zero() -> None:
    payload = {"speakers": [{"label": "화자0", "name": "UNKNOWN"}, {"label": "화자1"}]}
    parsed = meeting.parse_extraction(json.dumps(payload))
    assert parsed.speakers == (meeting.SpeakerRef("화자1"),)
    for refs, expected in (((meeting.SpeakerRef("화자0", "UNKNOWN"),), []),
                           ((meeting.SpeakerRef("화자0", "UNKNOWN"), meeting.SpeakerRef("화자1")),
                            ["- 화자: 화자1=미상"])):
        document = minutes.render(label="합성", kind="md", extraction=meeting.Extraction(speakers=refs),
                                  original_text="가.", sensitive=False, ref="fixture", now=datetime(2026, 9, 7))
        assert [line for line in document.splitlines() if line.startswith("- 화자:")] == expected


# --- 문장 조립: 흩어진 미상 낱말이 확신 있는 문장을 부수지 않는다 --------------------

local_attribution = importlib.import_module("stt_local_attribution")
diarize = importlib.import_module("stt_diarize")


def _w(text: str, start: int, end: int) -> object:
    return blocks.TimedWord(text, start, end)


def test_an_unattributed_word_does_not_fracture_a_confident_sentence() -> None:
    """노드 실측(2026-09-07): 낱말의 85.4%가 화자를 받는데 블록의 47.6%가 화자0 이었다.

    두 녹음 모두 배율이 같았다(12%→47.6%, 20.5%→49.7% — 각각 2.4배). 원인은 정책이 아니라
    조립이다: 화자1 런 한가운데의 근거 없는 낱말 하나가 `groupby(tag)` 에서 런을 셋으로
    가르고, 그 조각들이 각각 블록이 된다.

    문장의 화자는 **그 문장 안의 낱말 증거**로 정한다. 직전 문장을 보고 정하는 것이
    아니므로 금지된 화자 상속이 아니다 — 근거는 문장 밖으로 나가지 않는다.
    """
    words = (
        _w(" 오늘", 0, 400), _w(" 회의는", 400, 800),
        _w(" 음", 1050, 1150),
        _w(" 세시에", 1400, 1800), _w(" 합니다.", 1800, 2200),
    )
    turns = (diarize.Turn(0, 800, 0), diarize.Turn(1400, 2200, 0))

    made = local_attribution.sentences(words, turns)

    assert [s.speaker for s in made] == ["화자1"]
    assert made[0].text == "오늘 회의는 음 세시에 합니다."
    # 낱말은 하나도 잃지 않는다 — 문장 라벨만 요약이고 낱말 증거는 그대로 실린다.
    assert len(made[0].words) == 5


def test_a_real_speaker_change_still_splits_the_sentence() -> None:
    """흡수는 근거가 없을 때만이다 — 두 화자가 각각 1초 이상 가진 문장은 그대로 갈린다."""
    words = (
        _w(" 이건", 0, 900), _w(" 제가", 900, 1900),
        _w(" 아니요", 2100, 3000), _w(" 제가요", 3000, 3900),
    )
    turns = (diarize.Turn(0, 1900, 0), diarize.Turn(2100, 3900, 1))

    made = local_attribution.sentences(words, turns)

    assert [s.speaker for s in made] == ["화자1", "화자2"]


def test_a_sentence_without_any_evidence_stays_unknown() -> None:
    """증거가 아예 없으면 화자를 지어내지 않는다 — 화자0 은 여전히 화자0 이다."""
    words = (_w(" 무슨", 9000, 9400), _w(" 말인지.", 9400, 9800))
    turns = (diarize.Turn(0, 800, 0),)

    made = local_attribution.sentences(words, turns)

    assert [s.speaker for s in made] == ["화자0"]
    assert made[0].attribution is not None and made[0].attribution.kind == "UNKNOWN"
