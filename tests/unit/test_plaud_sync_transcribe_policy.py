"""Withheld-cloud retry policy; real pure stage with counted fake effects."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from automation.plaud_sync.fetch import CloudTranscript
from automation.plaud_sync.model import PlaudSyncState
from automation.plaud_sync.note import render_lifelog_body
from automation.plaud_sync.transcribe import process, run_step
from tests.unit.test_plaud_sync_transcribe import FakeEffects, _DRAFT_RECORDING, _RECORD, _SKIPPED, _fail, _ok

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
EMPTY_DRAFT = render_lifelog_body(
    replace(_DRAFT_RECORDING, summary_markdown="", transcript_text=""), extraction=_SKIPPED
)


class CountingEffects(FakeEffects):
    """Mutable call recorder; the production step and note renderer remain real."""
    def __init__(self) -> None:
        super().__init__(draft=EMPTY_DRAFT, summary="", cloud=CloudTranscript(""))
        self.cloud_calls: list[tuple[str, str]] = []

    def draft_body(self, recording_id: str) -> str | None:
        return self.draft

    def fetch_summary(self, recording_id: str) -> str:
        self.cloud_calls.append((recording_id, "summary"))
        return self.summary

    def fetch_transcript(self, recording_id: str) -> CloudTranscript:
        self.cloud_calls.append((recording_id, "transcript"))
        return self.cloud


def test_withheld_fallback_schedules_backoff_when_cloud_is_empty() -> None:
    # Given
    effects = CountingEffects()
    effects.results = [_fail(5)]
    record = replace(_RECORD, transcribe_attempts=1)
    # When
    outcome = process(record, effects=effects, max_attempts=2)
    # Then
    assert outcome == "retry"
    assert getattr(effects.commits[0][1], "next_transcribe_at", None) is not None


@pytest.mark.parametrize("attempts,hours", [(2, 1), (3, 2), (4, 4), (5, 8), (6, 16), (7, 24), (12, 24)])
def test_backoff_uses_last_attempt_time_when_failure_is_counted(attempts: int, hours: int) -> None:
    # Given
    effects = CountingEffects()
    effects.results = [_fail(5)]
    record = replace(_RECORD, transcribe_attempts=attempts - 1)
    # When
    process(record, effects=effects, max_attempts=2, give_up=20, now=lambda: NOW)
    # Then
    after = effects.commits[0][1]
    assert after.next_transcribe_at == (NOW + timedelta(hours=hours)).isoformat()
    assert after.transcribe_attempts == attempts


def test_cloud_is_checked_without_local_work_when_backoff_is_waiting() -> None:
    # Given
    effects = CountingEffects()
    record = replace(_RECORD, transcribe_attempts=2, next_transcribe_at=(NOW + timedelta(hours=1)).isoformat())
    # When
    outcome = process(record, effects=effects, max_attempts=2, now=lambda: NOW)
    # Then
    assert outcome == "waiting"
    assert effects.cloud_calls == [(record.recording_id, "summary"), (record.recording_id, "transcript")]
    assert effects.labels == []
    assert effects.commits == []


@pytest.mark.parametrize("summary,transcript", [("- recovered", ""), ("", "recovered transcript")])
def test_cloud_promotes_immediately_when_content_arrives_during_backoff(summary: str, transcript: str) -> None:
    # Given
    effects = CountingEffects()
    effects.summary, effects.cloud = summary, CloudTranscript(transcript)
    record = replace(_RECORD, transcribe_attempts=2, next_transcribe_at=(NOW + timedelta(hours=1)).isoformat())
    # When
    outcome = process(record, effects=effects, max_attempts=2, now=lambda: NOW)
    # Then
    assert outcome == "fallback"
    assert effects.labels == []
    after = effects.commits[0][1]
    assert (after.status, after.transcribe_attempts, after.next_transcribe_at) == ("planned", 2, None)


def test_local_runs_when_backoff_reaches_exact_deadline() -> None:
    # Given
    effects = CountingEffects()
    effects.results = [_ok()]
    record = replace(_RECORD, transcribe_attempts=2, next_transcribe_at=NOW.isoformat())
    # When
    outcome = process(record, effects=effects, max_attempts=2, now=lambda: NOW)
    # Then
    assert outcome == "planned"
    assert len(effects.labels) == 1
    assert effects.commits[0][1].next_transcribe_at is None


def test_abandons_when_total_failures_reach_give_up() -> None:
    # Given
    effects = CountingEffects()
    effects.results = [_fail(5)]
    record = replace(_RECORD, transcribe_attempts=4)
    # When
    outcome = process(record, effects=effects, max_attempts=2, now=lambda: NOW)
    # Then
    assert outcome == "abandoned"
    after = effects.commits[0][1]
    assert (after.status, after.transcribe_attempts, after.next_transcribe_at) == ("abandoned", 5, None)
    assert after.last_block_reason
    assert effects.commits[0][2] is None


def test_waiting_records_leave_slot_available_when_new_recordings_exist() -> None:
    # Given: waiting record wins the ordinary attempts/time ordering.
    effects = CountingEffects()
    effects.results = [_ok()]
    waiting = replace(_RECORD, transcribe_attempts=2, next_transcribe_at=(NOW + timedelta(hours=1)).isoformat())
    fresh = replace(_RECORD, recording_id="rec-new", transcribe_attempts=3)
    state = PlaudSyncState(1, None, {r.recording_id: r for r in (waiting, fresh)})
    # When
    outcomes = run_step(state, effects=effects, limit=1, now=lambda: NOW)
    # Then
    assert outcomes == (("rec-001", "waiting"), ("rec-new", "planned"))
    assert len(effects.labels) == 1


def test_no_local_attempt_when_legacy_record_already_exceeds_give_up() -> None:
    # Given
    effects = CountingEffects()
    record = replace(_RECORD, transcribe_attempts=9)
    # When
    outcome = process(record, effects=effects, max_attempts=2, now=lambda: NOW)
    # Then
    assert outcome == "abandoned"
    assert effects.labels == []
    assert effects.commits[0][1].transcribe_attempts == 9
