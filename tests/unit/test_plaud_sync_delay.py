"""Where a Plaud recording waited, and that a long one does not hold up the others.

2026-11-10 → 11-26: a recorder left on cut one recording into 24-hour pieces. They showed up in the
Plaud list days late, nothing on the node said how late, the day-long pieces took the one local slot
ahead of short recordings, a piece whose cloud check kept failing retried on every tick with a frozen
next time, and a piece whose transcript outgrew the relay input failed its field extraction forever.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from automation.omo_relay.runner import LIMIT
from automation.plaud_sync.fetch import fetch_recordings
from automation.plaud_sync.lifelog_extract import MAX_PROMPT_BYTES, build_prompt, extract
from automation.plaud_sync.lifelog_model import LifelogExtractError, LifelogExtraction
from automation.plaud_sync.mcp_client import PlaudMcpError
from automation.plaud_sync.model import PlaudSyncState, parse_record, serialize_record
from automation.plaud_sync.sync import plan_new_records
from automation.plaud_sync.transcribe import TranscribeError, candidates, run_step
from tests.unit.test_plaud_sync_fetch_recordings import (
    FakeClient,
    _file_row,
    _files_text,
    _note_item,
    _segment,
    _transcript,
)
from tests.unit.test_plaud_sync_sync import _NO_EXTRACTION, _recording
from tests.unit.test_plaud_sync_transcribe import _RECORD, _ok
from tests.unit.test_plaud_sync_transcribe_live import _FAKE_CLI, _effects
from tests.unit.test_plaud_sync_transcribe_policy import NOW, CountingEffects
from tests.unit.test_plaud_sync_watch_transcribe import _load_watch

_DAY_MS = 86_400_380
_SEEN = datetime(2026, 11, 23, 2, 50, tzinfo=UTC)


def _late_discovery() -> tuple[PlaudSyncState, tuple[str, ...]]:
    recording = _recording(id="rec-late", start_at="2026-11-10T07:39:32",
                           created_at="2026-11-22T21:00:00Z", duration_ms=_DAY_MS)
    result = plan_new_records(PlaudSyncState(1, None, {}), (recording,), now=_SEEN, policy_version=8,
                              extractor=_NO_EXTRACTION, initial_status="transcribing")
    return result.state, result.discovered_lines


def test_discovery_keeps_the_plaud_upload_time_and_length_and_logs_how_late_it_was_seen() -> None:
    # When
    state, lines = _late_discovery()
    # Then
    record = state.records["rec-late"]
    assert (record.plaud_created_at, record.duration_ms) == ("2026-11-22T21:00:00Z", _DAY_MS)
    assert parse_record(serialize_record(record)) == record
    (line,) = lines
    assert line.startswith("plaud-sync: recording_id=rec-late outcome=discovered ")
    assert "plaud_created_at=2026-11-22T21:00:00Z" in line and "lag_days=12.8" in line


def test_a_recording_seen_late_in_the_lookback_window_gets_a_warning_line() -> None:
    # Given
    recording = _recording(id="rec-late", start_at="2026-11-10T07:39:32", duration_ms=_DAY_MS)
    result = plan_new_records(PlaudSyncState(1, None, {}), (recording,), now=_SEEN, policy_version=8,
                              extractor=_NO_EXTRACTION, initial_status="transcribing")
    # When / Then: 12.8 days is past half of a 14-day window, not of a 60-day one.
    (warning,) = result.late_lines(lookback_days=14)
    assert "recording_id=rec-late outcome=late" in warning and "lookback_days=14" in warning
    assert result.late_lines(lookback_days=60) == ()


def test_the_cron_poll_asks_for_unknown_recordings_over_a_wide_window_and_journals_what_it_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    from automation.plaud_sync import fetch, mcp_client

    watch = _load_watch(monkeypatch, tmp_path)
    asked: dict[str, object] = {}
    late = _recording(id="rec-late", start_at="2026-11-10T07:39:32", created_at="2026-11-22T21:00:00Z",
                      duration_ms=_DAY_MS)

    class Client:
        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_exception: object) -> bool:
            return False

    def fetched(client: Client, *, date_from: str, known: frozenset[str], min_duration_ms: int) -> tuple[object, ...]:
        asked.update(date_from=date_from, known=known, min_duration_ms=min_duration_ms)
        return (late,)

    monkeypatch.setattr(mcp_client, "PlaudMcpClient", Client)
    monkeypatch.setattr(fetch, "fetch_recordings", fetched)
    known = replace(_RECORD, recording_id="rec-known", status="written", aliases=("of_rec-moved",))
    # When
    state = watch._discover(PlaudSyncState(1, None, {"rec-known": known}), _SEEN)
    # Then
    assert state.records["rec-late"].plaud_created_at == "2026-11-22T21:00:00Z"
    assert asked == {"date_from": "2026-09-24", "known": frozenset({"rec-known", "rec-moved"}), "min_duration_ms": 5000}
    err = capsys.readouterr().err
    assert "recording_id=rec-late outcome=discovered" in err and "outcome=late" not in err


def test_discovery_reads_note_and_transcript_only_for_long_enough_recordings_it_does_not_know() -> None:
    # Given: a known recording (its folder move added of_), a new one and a 4-second one.
    short = {**_file_row("rec-short"), "duration": 4000}
    client = FakeClient(
        pages=[_files_text(_file_row("of_rec-known"), _file_row("rec-new"), short)],
        notes={"rec-new": [_note_item("요약")]},
        transcripts={"rec-new": _transcript([_segment("전문", start_ms=0)])},
    )
    # When
    recordings = fetch_recordings(client, date_from=None, known=frozenset({"rec-known"}), min_duration_ms=5000)
    # Then
    assert [recording.id for recording in recordings] == ["rec-new", "rec-short"]
    assert {arguments.get("file_id") for name, arguments in client.calls if name != "list_files"} == {"rec-new"}
    assert (recordings[1].duration_ms, recordings[1].summary_markdown) == (4000, "")


def test_a_listing_cut_by_the_page_cap_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    # Given: one new recording per page, more pages than the cap reads.
    pages = [_files_text(_file_row(f"rec-{index:03d}")) for index in range(12)]
    client = FakeClient(pages=pages)
    known = frozenset(f"rec-{index:03d}" for index in range(12))
    # When
    _ = fetch_recordings(client, date_from="2026-10-01", page_size=1, known=known)
    # Then
    assert "plaud-sync: list_files outcome=page_cap" in capsys.readouterr().err


def _due(**changes: object) -> object:
    due = {"transcribe_attempts": 2, "last_block_reason": "클라우드 폴백 보류: 요약과 전사가 없다",
           "next_transcribe_at": (NOW - timedelta(days=5)).isoformat()}
    return replace(_RECORD, **{**due, **changes})


def _cloud_down(recording_id: str) -> str:
    raise TranscribeError("get_note: PlaudMcpError: API error: 429 Too Many Requests", counted=False)


def test_a_due_record_whose_cloud_check_fails_moves_its_next_time_and_still_transcribes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the 11/13 piece — due since 11/21, every cloud check failing.
    effects = CountingEffects()
    effects.results = [_ok()]
    monkeypatch.setattr(effects, "fetch_summary", _cloud_down)
    record = _due()
    # When
    outcomes = run_step(PlaudSyncState(1, None, {"rec-001": record}), effects=effects, limit=1, now=lambda: NOW)
    # Then
    assert outcomes == (("rec-001", "planned"),)
    moved = effects.commits[0][1]
    assert moved.next_transcribe_at == (NOW + timedelta(hours=1)).isoformat()
    assert moved.last_recheck_error == "get_note: PlaudMcpError: API error: 429 Too Many Requests"
    assert moved.last_block_reason == record.last_block_reason
    assert effects.commits[1][0] == moved, "the local attempt must commit on top of the moved record"
    assert effects.commits[1][1].status == "planned" and len(effects.labels) == 1


def test_a_due_record_whose_cloud_check_fails_waits_out_its_backoff_without_a_free_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    effects = CountingEffects()
    monkeypatch.setattr(effects, "fetch_summary", _cloud_down)
    # When
    first = run_step(PlaudSyncState(1, None, {"rec-001": _due()}), effects=effects, limit=0, now=lambda: NOW)
    moved = effects.commits[-1][1]
    later = NOW + timedelta(minutes=30)
    second = run_step(PlaudSyncState(1, None, {"rec-001": moved}), effects=effects, limit=1, now=lambda: later)
    # Then
    assert first == (("rec-001", "retry"),) and second == (("rec-001", "retry"),)
    assert moved.next_transcribe_at == (NOW + timedelta(hours=1)).isoformat()
    assert effects.commits[-1][1].next_transcribe_at == moved.next_transcribe_at
    assert effects.labels == []


def test_a_record_past_the_failure_cap_whose_cloud_check_fails_backs_off_without_local_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    effects = CountingEffects()
    monkeypatch.setattr(effects, "fetch_summary", _cloud_down)
    record = _due(transcribe_attempts=5)
    # When
    outcomes = run_step(PlaudSyncState(1, None, {"rec-001": record}), effects=effects, limit=1, now=lambda: NOW)
    # Then
    assert outcomes == (("rec-001", "retry"),)
    assert effects.commits[0][1].next_transcribe_at == (NOW + timedelta(hours=8)).isoformat()
    assert effects.labels == []


def test_live_cloud_errors_keep_the_api_message_in_the_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    class RateLimited:
        def __enter__(self) -> RateLimited:
            raise PlaudMcpError("Failed to get note: Error: API error: 429 Too Many Requests")

        def __exit__(self, *_exception: object) -> bool:
            return False

    effects = _effects(tmp_path)
    monkeypatch.setattr(type(effects), "_mcp_client", lambda self: RateLimited())
    # When
    with pytest.raises(TranscribeError) as caught:
        _ = effects.fetch_summary("rec-001")
    # Then
    assert caught.value.counted is False
    assert "429 Too Many Requests" in caught.value.reason


def _day_long_transcript() -> str:
    blocks = [f"[{index // 60:02d}:{index % 60:02d}:00] 화자1\n오늘 이야기한 내용 {index}번째 문장입니다." for index in range(6000)]
    return "- 화자: 화자1=미상\n\n" + "\n\n".join(blocks)


def test_a_day_long_transcript_is_sent_to_extraction_under_the_relay_input_limit() -> None:
    # Given: 2026-11-22 a 276,748-byte transcript made the relay refuse every extraction unread.
    transcript = _day_long_transcript()
    assert len(transcript.encode("utf-8")) > LIMIT
    sent: list[str] = []

    def complete(prompt: str) -> str:
        sent.append(prompt)
        return '{"summary": ["- 하루 기록"]}'

    recording = replace(_recording(), transcript_text=transcript)
    # When
    outcome = extract(recording, template="요약:{{SUMMARY}}\n전사:{{TRANSCRIPT}}", complete=complete)
    # Then
    assert isinstance(outcome, LifelogExtraction)
    (prompt,) = sent
    assert len(prompt.encode("utf-8")) <= MAX_PROMPT_BYTES < LIMIT
    assert "- 화자: 화자1=미상" in prompt and "0번째 문장" in prompt
    assert any(f"{index}번째 문장" in prompt for index in range(5950, 6000)), "the end of the day is kept"


def test_a_transcript_that_fits_is_sent_whole() -> None:
    # Given
    sent: list[str] = []
    recording = _recording()
    # When
    _ = extract(recording, template="{{SUMMARY}}|{{TRANSCRIPT}}", complete=lambda prompt: sent.append(prompt) or "{}")
    # Then
    assert sent == [build_prompt("{{SUMMARY}}|{{TRANSCRIPT}}", summary=recording.summary_markdown,
                                 transcript=recording.transcript_text)]


def test_an_extraction_failure_keeps_its_cause_in_the_reason() -> None:
    # Given
    def refuse(prompt: str) -> str:
        raise RuntimeError("omo relay input too large (300000 bytes > 262144)")

    # When
    with pytest.raises(LifelogExtractError) as caught:
        _ = extract(_recording(), template="{{TRANSCRIPT}}", complete=refuse)
    # Then
    assert "input too large" in str(caught.value)


def test_local_transcription_logs_its_start_and_end_for_each_recording(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    fake = tmp_path / "fake_cli.py"
    fake.write_text(_FAKE_CLI, encoding="utf-8")
    effects = _effects(tmp_path, SPEECHTOTEXT_CLI=str(fake), FAKE_CLI_LOG=str(tmp_path / "cli.json"))
    audio = tmp_path / "rec-001.mp3"
    audio.write_bytes(b"\x00")
    # When
    _ = effects.transcribe(audio, "label")
    _ = effects.transcribe(audio, "label")
    # Then
    err = capsys.readouterr().err
    assert "plaud-sync: recording_id=rec-001 transcribe=start" in err
    assert re.search(r"plaud-sync: recording_id=rec-001 transcribe=end rc=0 seconds=\d+", err)
    assert "plaud-sync: recording_id=rec-001 transcribe=reused" in err


def test_a_short_recording_is_transcribed_before_a_day_long_one_found_earlier() -> None:
    # Given: 11/23..26 the day-long pieces held the one local slot ahead of everything newer.
    day = replace(_RECORD, recording_id="rec-day", recorded_at="2026-11-10T07:39:32", duration_ms=_DAY_MS)
    legacy = replace(_RECORD, recording_id="rec-legacy", recorded_at="2026-11-01T00:00:00")
    short = replace(_RECORD, recording_id="rec-short", recorded_at="2026-11-26T01:00:00", duration_ms=600_000)
    retried = replace(short, recording_id="rec-retried", recorded_at="2026-11-20T00:00:00", transcribe_attempts=1)
    state = PlaudSyncState(1, None, {record.recording_id: record for record in (day, legacy, short, retried)})
    # When
    order = [record.recording_id for record in candidates(state, limit=4)]
    # Then: a record without a known length counts as long.
    assert order == ["rec-short", "rec-retried", "rec-legacy", "rec-day"]
