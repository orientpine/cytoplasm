from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime

from automation.term_correction import FUZZY, Correction
from automation.plaud_sync.binding import PlaudHashFields, plaud_action_hash
from automation.plaud_sync.lifelog_model import (
    ExtractionSkipped,
    LifelogExtractError,
    LifelogExtraction,
    LifelogRecording as _ModelRecording,
)
from automation.plaud_sync.model import PlaudSyncRecord, PlaudSyncState
from automation.plaud_sync.note import LifelogRecording
from automation.plaud_sync.sync import plan_new_records, poll_due

_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


def _NO_EXTRACTION(recording: _ModelRecording) -> ExtractionSkipped:
    return ExtractionSkipped("테스트")


_BASE = LifelogRecording(
    id="rec-001",
    name="standup",
    created_at="2026-09-01T08:05:00Z",
    start_at="2026-09-01T08:00:00Z",
    duration_ms=60000,
    summary_markdown="- 결정",
    transcript_text="말씀",
)


def _recording(**overrides: object) -> LifelogRecording:
    return replace(_BASE, **overrides)


def _planned_record(state: PlaudSyncState, recording_id: str) -> PlaudSyncRecord:
    record = state.records[recording_id]
    assert record.status == "planned"
    return record


def test_discovery_skips_short_recordings_when_below_default_gate() -> None:
    # Given: content exists, so the duration gate alone must prevent freezing.
    state = PlaudSyncState(1, None, {})
    short = replace(_BASE, duration_ms=1000)
    # When
    result = plan_new_records(state, (short,), now=_NOW, policy_version=8, extractor=_NO_EXTRACTION)
    # Then
    assert result.skipped == (short.id,)
    assert result.state.records == {}
    assert result.bodies == {}
    assert result.skipped_durations == ((short.id, 1000),)


def test_discovery_accepts_exact_threshold_when_gate_is_overridden() -> None:
    # Given
    state = PlaudSyncState(1, None, {})
    recording = replace(_BASE, duration_ms=1000)
    # When
    result = plan_new_records(state, (recording,), now=_NOW, policy_version=8,
                              extractor=_NO_EXTRACTION, min_duration_ms=1000)
    # Then
    assert result.planned == (recording.id,)


def test_poll_due_when_never_polled() -> None:
    state = PlaudSyncState(version=1, last_poll_at=None, records={})
    assert poll_due(state, _NOW, 1800)


def test_poll_not_due_within_interval() -> None:
    state = PlaudSyncState(
        version=1, last_poll_at="2026-09-02T11:45:00+00:00", records={}
    )
    assert not poll_due(state, _NOW, 1800)


def test_poll_due_after_interval_or_bad_watermark() -> None:
    stale = PlaudSyncState(version=1, last_poll_at="2026-09-02T11:00:00+00:00", records={})
    broken = PlaudSyncState(version=1, last_poll_at="not-a-time", records={})
    assert poll_due(stale, _NOW, 1800)
    assert poll_due(broken, _NOW, 1800)


def test_plan_new_records_freezes_body_and_binds_hash() -> None:
    state = PlaudSyncState(version=1, last_poll_at=None, records={})
    result = plan_new_records(state, (_recording(),), now=_NOW, policy_version=8, extractor=_NO_EXTRACTION)
    record = _planned_record(result.state, "rec-001")
    body = result.bodies["rec-001"]
    assert result.planned == ("rec-001",)
    assert body.index("## 요약") < body.index("## 전문")
    assert record.body_sha256 == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert record.action_hash == plaud_action_hash(
        PlaudHashFields(
            recording_id="rec-001",
            note_relpath=record.note_relpath,
            note_title=record.note_title,
            body_sha256=record.body_sha256,
        )
    )
    assert record.kind == "obsidian-write"
    assert record.recorded_at == "2026-09-01T08:00:00Z"
    assert result.state.last_poll_at == _NOW.isoformat()


def test_plan_new_records_corrects_the_frozen_body_and_reports_the_words() -> None:
    # 교정은 노트를 얼리는 그 자리에서 끝나야 한다 — 카드가 붙는 sha 는 언 본문의 것이다.
    state = PlaudSyncState(version=1, last_poll_at=None, records={})

    result = plan_new_records(
        state,
        (_recording(summary_markdown="항정기술과 회의했다.", transcript_text="항정기술과 회의했다."),),
        now=_NOW,
        policy_version=8,
        extractor=_NO_EXTRACTION,
        glossary=(("한전기술", "한전기술"),),
    )

    body = result.bodies["rec-001"]
    summary, transcript = body.split("## 전문")
    assert "한전기술과 회의했다." in summary
    assert "항정기술과 회의했다." in transcript, "전문은 인식된 그대로 남는다"
    assert result.corrections == (
        ("standup", (Correction(before="항정기술", after="한전기술", term="한전기술", kind=FUZZY),)),
    )
    record = _planned_record(result.state, "rec-001")
    assert record.body_sha256 == hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_plan_new_records_skips_known_ids() -> None:
    first = plan_new_records(
        PlaudSyncState(version=1, last_poll_at=None, records={}),
        (_recording(),),
        now=_NOW,
        policy_version=8,
        extractor=_NO_EXTRACTION,
    )
    second = plan_new_records(
        first.state,
        (_recording(), _recording(id="rec-002")),
        now=_NOW,
        policy_version=8,
        extractor=_NO_EXTRACTION,
    )
    assert second.planned == ("rec-002",)
    assert "rec-001" not in second.bodies


def test_plan_new_records_reports_unplannable_recordings() -> None:
    state = PlaudSyncState(version=1, last_poll_at=None, records={})
    result = plan_new_records(
        state,
        (_recording(id="rec-bad", created_at="nope", start_at=""),),
        now=_NOW,
        policy_version=8,
        extractor=_NO_EXTRACTION,
    )
    assert result.planned == ()
    assert result.skipped == ("rec-bad",)
    assert result.state.last_poll_at == _NOW.isoformat()


def test_plan_new_records_leaves_an_empty_recording_for_a_later_poll() -> None:
    # 2026-09-02 실측: 전사가 끝나지 않은 녹음이 빈 노트로 동결돼 승인 카드까지 올라갔다.
    state = PlaudSyncState(version=1, last_poll_at=None, records={})
    result = plan_new_records(
        state,
        (_recording(id="rec-empty", summary_markdown="  ", transcript_text=""),),
        now=_NOW,
        policy_version=8,
        extractor=_NO_EXTRACTION,
    )
    assert result.planned == ()
    assert result.skipped == ("rec-empty",)
    assert "rec-empty" not in result.state.records


# ---- v2 (B안): 추출기 주입 · 추출 실패는 동결하지 않고 다음 폴로 미룬다 ----


def test_plan_new_records_passes_each_new_recording_to_the_extractor_and_freezes_its_fields() -> None:
    seen: list[str] = []

    def extractor(recording: _ModelRecording) -> LifelogExtraction:
        seen.append(recording.id)
        return LifelogExtraction(people=("김철수",), places=("구내식당",))

    state = PlaudSyncState(version=1, last_poll_at=None, records={})
    result = plan_new_records(
        state, (_recording(),), now=_NOW, policy_version=8, extractor=extractor
    )

    assert seen == ["rec-001"]
    assert result.planned == ("rec-001",)
    assert result.deferred == ()
    # 한눈에는 녹음 한 줄과 진단만 싣는다(2026-09-07) — 사람·장소는 요약 본문이 말한다.
    assert "- 녹음:: " in result.bodies["rec-001"]
    assert "사람::" not in result.bodies["rec-001"]


def test_plan_new_records_defers_a_recording_whose_extraction_failed_this_poll() -> None:
    # 전송·파싱 실패는 저하된 노트를 영구 동결하는 대신 다음 폴에 재시도한다(빈 요약 skip 과 같은 원칙).
    def extractor(recording: _ModelRecording) -> LifelogExtraction:
        raise LifelogExtractError("추출 모델 시간 초과")

    state = PlaudSyncState(version=1, last_poll_at=None, records={})
    result = plan_new_records(
        state, (_recording(),), now=_NOW, policy_version=8, extractor=extractor
    )

    assert result.planned == ()
    assert result.skipped == ()
    assert result.deferred == ("rec-001",)
    assert "rec-001" not in result.state.records
    assert "rec-001" not in result.bodies
    assert result.state.last_poll_at == _NOW.isoformat()


def test_plan_new_records_does_not_call_the_extractor_for_known_or_empty_recordings() -> None:
    calls: list[str] = []

    def extractor(recording: _ModelRecording) -> ExtractionSkipped:
        calls.append(recording.id)
        return ExtractionSkipped("테스트")

    first = plan_new_records(
        PlaudSyncState(version=1, last_poll_at=None, records={}),
        (_recording(),),
        now=_NOW,
        policy_version=8,
        extractor=extractor,
    )
    second = plan_new_records(
        first.state,
        (_recording(), _recording(id="rec-empty", summary_markdown="", transcript_text="")),
        now=_NOW,
        policy_version=8,
        extractor=extractor,
    )

    assert calls == ["rec-001"]
    assert second.skipped == ("rec-empty",)
    assert second.deferred == ()


def test_plan_new_records_leaves_a_transcribing_draft_for_finalize_to_extract() -> None:
    # 로컬 전사(PR #383) 가 끝난 뒤 transcribe.finalize 가 그 전사로 추출한다 — 클라우드 초안에서
    # LLM 을 부르면 같은 녹음에 두 번 쓰고, 더 나쁜 입력(speaker_1 라벨)으로 뽑는다.
    def extractor(recording: _ModelRecording) -> LifelogExtraction:
        raise AssertionError("cloud draft must not reach the LLM")

    state = PlaudSyncState(version=1, last_poll_at=None, records={})
    result = plan_new_records(
        state,
        (_recording(),),
        now=_NOW,
        policy_version=8,
        extractor=extractor,
        initial_status="transcribing",
    )

    assert result.planned == ("rec-001",)
    assert result.deferred == ()
    assert result.state.records["rec-001"].status == "transcribing"
    assert "- 추출:: 생략 (로컬 전사 뒤 추출)\n" in result.bodies["rec-001"]


def test_plan_new_records_freezes_a_cloud_empty_recording_when_the_node_will_transcribe_it() -> None:
    # 2026-09-05 실측: Plaud 가 요약도 전사도 만들지 않은 64분 녹음이 매 폴 skipped 로 남아 로컬 전사에
    # 도달하지 못했다 — 노드가 전사하는 경로에서 빈 클라우드 초안은 건너뛸 이유가 아니라 그 경로의 존재 이유다.
    state = PlaudSyncState(version=1, last_poll_at=None, records={})
    result = plan_new_records(
        state,
        (_recording(id="rec-empty", summary_markdown="", transcript_text=""),),
        now=_NOW,
        policy_version=8,
        extractor=_NO_EXTRACTION,
        initial_status="transcribing",
    )

    assert result.planned == ("rec-empty",)
    assert result.skipped == ()
    assert result.state.records["rec-empty"].status == "transcribing"
    assert "rec-empty" in result.bodies
