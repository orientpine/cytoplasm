"""Local transcription must preserve the discovery instant when get_file is naive UTC.

This stays separate from the fixed FS3 evidence tests: it compares an explicit-UTC
reference input against the naive get_file input (the demonstrated bug treated the
offset-free get_file string as local time) and proves promotion never rewrites the
already-frozen discovery timestamp. It does not claim every list_files payload carries
an offset.
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from automation.plaud_sync.audio import AudioSource, parse_source
from automation.plaud_sync.lifelog_fields import local_stamp
from automation.plaud_sync.lifelog_model import ExtractionSkipped, LifelogRecording
from automation.plaud_sync.note import plan_lifelog_note
from automation.plaud_sync.transcribe_text import recording_from_source

_SEOUL = ZoneInfo("Asia/Seoul")
_SKIPPED = ExtractionSkipped("test")
_GET_FILE = {
    "id": "synthetic-local-utc",
    "name": "2026-04-03 18:04:27 local transcription",
    "created_at": "2026-04-03T09:04:27",
    "start_at": "2026-04-03T09:04:27",
    "duration": 3_840_000,
    "presigned_url": "https://example.invalid/synthetic-local-utc.mp3",
}


def test_note_when_get_file_timestamp_is_naive_utc_then_body_uses_the_kst_title_instant() -> None:
    # Given: get_file sends the UTC instant without an offset.
    source = parse_source(json.dumps(_GET_FILE), "synthetic-local-utc")
    recording = recording_from_source(
        source, "", summary="", transcript_text="", transcript_source="로컬 전사 synthetic"
    )

    # When
    plan = plan_lifelog_note(recording, extraction=_SKIPPED, tz=_SEOUL)

    # Then
    assert plan.title.startswith("2026-04-03 18:04:27")
    assert "created: 2026-04-03T18:04:27" in plan.body
    assert "- 녹음:: 2026-04-03 (금) 18:04 · 64분 0초" in plan.body
    assert "· 2026-04-03T18:04:27 · 64분 0초" in plan.body


def test_recording_from_source_when_get_file_is_naive_utc_then_matches_the_list_files_discovery_stamp() -> None:
    """The finalize path receives naive get_file fields; record.recorded_at remains discovery state."""
    # Given: the discovery reference carries an explicit UTC offset while get_file carries the same instant naively.
    discovery = LifelogRecording(
        id="synthetic-local-utc",
        name="local transcription",
        created_at="2026-04-03T09:04:27+00:00",
        start_at="2026-04-03T09:04:27+00:00",
        duration_ms=3_840_000,
        summary_markdown="",
        transcript_text="",
    )
    source = AudioSource(
        recording_id="synthetic-local-utc",
        name="local transcription",
        created_at="2026-04-03T09:04:27",
        start_at="2026-04-03T09:04:27",
        duration_ms=3_840_000,
        url="https://example.invalid/synthetic-local-utc.mp3",
        suffix=".mp3",
    )

    # When
    finalized = recording_from_source(source, "", summary="", transcript_text="", transcript_source="로컬 전사 synthetic")

    # Then: finalize uses the discovery instant and does not mutate persisted recorded_at.
    assert local_stamp(finalized, _SEOUL) == local_stamp(discovery, _SEOUL) == datetime(2026, 4, 3, 18, 4, 27)
