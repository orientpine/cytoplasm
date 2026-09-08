"""Summary selection through the real extractor, renderer and transcription stage."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from automation.plaud_sync.lifelog_extract import parse_extraction
from automation.plaud_sync.lifelog_extract_live import build_extractor
from automation.plaud_sync.lifelog_model import ExtractionOutcome, LifelogExtraction, LifelogRecording
from automation.plaud_sync.note import render_lifelog_body, split_lifelog_body
from automation.plaud_sync.transcribe import process
from tests.unit.test_plaud_sync_transcribe import _DRAFT_RECORDING, _RECORD, _ok
from tests.unit.test_plaud_sync_transcribe_policy import CountingEffects, NOW

SUMMARY = "- 산책 중 다음 일정을 논의했다.\n- 금요일에 만나기로 했다.\n- 장소는 아직 정하지 않았다."


@pytest.mark.parametrize("cloud", ["", "   "])
def test_note_uses_extraction_summary_when_plaud_summary_is_empty(cloud: str) -> None:
    # Given
    extraction = parse_extraction(json.dumps({"summary": SUMMARY}))
    recording = replace(_DRAFT_RECORDING, summary_markdown=cloud)
    # When
    body = render_lifelog_body(recording, extraction=extraction)
    # Then
    assert split_lifelog_body(body)[0] == SUMMARY


def test_entire_note_is_byte_identical_when_plaud_summary_exists() -> None:
    # Given: generated summary differs from the Plaud source.
    extraction = parse_extraction(json.dumps({"people": ["참여자"], "summary": SUMMARY}))
    expected = render_lifelog_body(_DRAFT_RECORDING, extraction=LifelogExtraction(people=("참여자",)))
    # When
    actual = render_lifelog_body(_DRAFT_RECORDING, extraction=extraction)
    # Then
    assert actual.encode() == expected.encode()


def test_local_summary_is_frozen_with_fields_when_single_llm_call_finishes() -> None:
    # Given: real prompt loader, sensitivity gate, parser, note renderer and finalize.
    calls: list[str] = []
    def complete(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"summary": SUMMARY, "people": ["참여자"]})
    extractor = build_extractor({}, repo_root=Path(__file__).resolve().parents[2], complete=complete)
    class Extracting(CountingEffects):
        def extract(self, recording: LifelogRecording) -> ExtractionOutcome:
            return extractor(recording)
    effects = Extracting()
    effects.results = [_ok()]
    # When
    outcome = process(_RECORD, effects=effects, max_attempts=2, now=lambda: NOW)
    # Then
    assert outcome == "planned"
    assert len(calls) == 1
    body = effects.commits[0][2]
    assert body is not None
    assert split_lifelog_body(body)[0] == SUMMARY
    # 사람 위키링크는 더 이상 렌더되지 않는다(2026-09-07) — 얼어붙는 것은 요약이고,
    # 그 요약이 본문에 실렸는지는 위 split_lifelog_body 단언이 이미 고정한다.
    assert "[[참여자]]" not in body
    assert "- 녹음:: " in body


def test_missing_summary_is_compatible_when_legacy_extraction_is_parsed() -> None:
    # Given
    raw = '{"people": ["참여자"]}'
    # When
    extraction = parse_extraction(raw)
    # Then
    assert extraction.summary == ""


def test_note_says_why_the_summary_is_missing_when_the_model_gave_none() -> None:
    """'- (요약 없음)' 만으로는 무엇이 잘못됐는지 알 수 없다.

    실측 2026-09-04 노트: 사람 18명·장소 3곳·결정 4건은 채워졌는데 요약만 비어
    소유자는 모델이 죽은 것인지, 게이트가 막은 것인지, 녹음이 빈 것인지 구별할 수
    없었다. 게이트로 건너뛴 경우에는 이미 '- 추출:: 생략 (사유)' 가 있으므로,
    추출이 돌았는데도 요약이 없는 이 경우에도 같은 자리에 사유를 적는다.
    """
    # Given: 추출은 성공했다(사람을 찾았다) — 요약만 비었다
    extraction = LifelogExtraction(people=("참여자",))
    recording = replace(_DRAFT_RECORDING, summary_markdown="")

    # When
    body = render_lifelog_body(recording, extraction=extraction)

    # Then
    assert "- 요약:: 없음" in body
    assert split_lifelog_body(body)[0] == "- (요약 없음)"


def test_note_does_not_explain_a_summary_that_exists() -> None:
    """요약이 있으면 사유 줄은 나타나지 않는다 — 정상 노트의 바이트는 그대로다."""
    # Given
    extraction = parse_extraction(json.dumps({"summary": SUMMARY}))
    recording = replace(_DRAFT_RECORDING, summary_markdown="")

    # When
    body = render_lifelog_body(recording, extraction=extraction)

    # Then
    assert "- 요약::" not in body
