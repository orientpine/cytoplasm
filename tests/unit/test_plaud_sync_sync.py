from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from automation.plaud_sync.binding import PlaudHashFields, plaud_action_hash
from automation.plaud_sync.fetch import fetch_recordings
from automation.plaud_sync.lifelog_model import (
    ExtractionSkipped,
    LifelogExtractError,
    LifelogExtraction,
    LifelogRecording as _ModelRecording,
)
from automation.plaud_sync.mcp_client import JsonObject, JsonValue, PlaudMcpError
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


def _wrap(text: str) -> JsonObject:
    return {"content": [{"type": "text", "text": text}]}


class _Queue(list[object]):  # noqa: FURB189 - marks sequential per-call responses, not a payload
    pass


class FakeClient:
    def __init__(
        self,
        *,
        pages: list[str],
        notes: dict[str, object] | None = None,
        transcripts: dict[str, object] | None = None,
    ) -> None:
        self.pages = list(pages)
        self.notes = notes or {}
        self.transcripts = transcripts or {}
        self.calls: list[tuple[str, JsonObject]] = []

    def call_tool(
        self, name: str, arguments: JsonObject, timeout: float = 60.0
    ) -> JsonObject:
        self.calls.append((name, dict(arguments)))
        if name == "list_files":
            raw_page = arguments.get("page", 1)
            page = raw_page if isinstance(raw_page, int) else 1
            return _wrap(self.pages[page - 1] if page - 1 < len(self.pages) else _files_text())
        file_id = str(arguments.get("file_id"))
        book = self.notes if name == "get_note" else self.transcripts
        entry = book[file_id]
        if isinstance(entry, _Queue):
            entry = entry.pop(0) if len(entry) > 1 else entry[0]
        if isinstance(entry, PlaudMcpError):
            raise entry
        return _wrap(entry if isinstance(entry, str) else json.dumps(entry))


def _files_text(*rows: JsonObject) -> str:
    return json.dumps({"type": "files", "data": list(rows), "page": 1, "page_size": 20})


def _file_row(recording_id: str) -> JsonObject:
    return {
        "id": recording_id,
        "name": f"recording {recording_id}",
        "created_at": "2026-09-01T08:05:00Z",
        "start_at": "2026-09-01T08:00:00Z",
        "duration": 60000,
    }


def _note_item(content: str, *, title: str = "Summary", err: int = 0) -> JsonObject:
    return {
        "data_id": "d" * 50,
        "data_type": "auto_sum_note",
        "data_title": title,
        "data_tab_name": title,
        "data_content": content,
        "data_link": "",
        "data_error_code": err,
    }


def _segment(content: str, *, start_ms: int, speaker: str = "화자1") -> JsonObject:
    return {
        "start_time": start_ms,
        "end_time": start_ms + 1000,
        "content": content,
        "speaker": speaker,
        "original_speaker": speaker,
    }


def _transcript(segments: list[JsonValue], *, next_cursor: str | None = None) -> JsonObject:
    return {
        "file_id": "f" * 32,
        "block": "default",
        "total": len(segments),
        "offset": 0,
        "limit": 50,
        "returned": len(segments),
        "next_cursor": next_cursor,
        "segments": segments,
    }


def test_fetch_recordings_parses_real_note_and_transcript_schema() -> None:
    client = FakeClient(
        pages=[_files_text(_file_row("rec-001"))],
        notes={"rec-001": [_note_item("- 결정사항")]},
        transcripts={
            "rec-001": _transcript(
                [_segment("안녕하세요", start_ms=1000), _segment("다음 안건", start_ms=3000, speaker="화자2")]
            )
        },
    )
    recordings = fetch_recordings(client, date_from="2026-08-20")
    assert len(recordings) == 1
    recording = recordings[0]
    assert recording.id == "rec-001"
    assert recording.duration_ms == 60000
    assert "- 결정사항" in recording.summary_markdown
    assert "안녕하세요" in recording.transcript_text
    assert "다음 안건" in recording.transcript_text
    assert "화자1" in recording.transcript_text
    assert "00:01" in recording.transcript_text
    assert ("get_note", {"file_id": "rec-001"}) in client.calls


def test_fetch_recordings_empty_payloads_render_as_blank() -> None:
    client = FakeClient(
        pages=[_files_text(_file_row("rec-001"))],
        notes={"rec-001": []},
        transcripts={"rec-001": _transcript([])},
    )
    recording = fetch_recordings(client, date_from=None)[0]
    assert recording.summary_markdown == ""
    assert recording.transcript_text == ""


def test_fetch_recordings_tries_supported_blocks_until_one_answers_and_records_it() -> None:
    class BlockClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, JsonObject]] = []

        def call_tool(
            self, name: str, arguments: JsonObject, timeout: float = 60.0
        ) -> JsonObject:
            self.calls.append((name, dict(arguments)))
            if name == "list_files":
                return _wrap(_files_text(_file_row("rec-001")))
            if name == "get_note":
                return _wrap(json.dumps([_note_item("요약")]))
            block = arguments.get("block")
            if block == "transaction":
                raise PlaudMcpError("transaction block failed")
            if block == "outline":
                return _wrap(json.dumps(_transcript([], next_cursor=None)))
            assert block == "transaction_polish"
            return _wrap(json.dumps(_transcript([_segment("정리된 전문", start_ms=0)])))

    client = BlockClient()
    recording = fetch_recordings(client, date_from=None)[0]

    assert recording.transcript_text.endswith("정리된 전문")
    assert recording.transcript_source == "PLAUD 클라우드 전사(transaction_polish 블록)"
    transcript_blocks = [arguments["block"] for name, arguments in client.calls if name == "get_transcript"]
    assert transcript_blocks == ["transaction", "outline", "transaction_polish"]


def test_fetch_recordings_accepts_plain_text_payloads() -> None:
    client = FakeClient(
        pages=[_files_text(_file_row("rec-001"))],
        notes={"rec-001": "그냥 마크다운 요약"},
        transcripts={"rec-001": "그냥 전문 텍스트"},
    )
    recording = fetch_recordings(client, date_from=None)[0]
    assert recording.summary_markdown == "그냥 마크다운 요약"
    assert recording.transcript_text == "그냥 전문 텍스트"


def test_fetch_recordings_follows_transcript_next_cursor() -> None:
    client = FakeClient(
        pages=[_files_text(_file_row("rec-001"))],
        notes={"rec-001": [_note_item("요약")]},
        transcripts={
            "rec-001": _Queue(
                [
                    _transcript([_segment("앞부분", start_ms=0)], next_cursor="next-1"),
                    _transcript([_segment("뒷부분", start_ms=5000)], next_cursor=None),
                ]
            )
        },
    )
    recording = fetch_recordings(client, date_from=None)[0]
    assert "앞부분" in recording.transcript_text
    assert "뒷부분" in recording.transcript_text
    transcript_calls = [call for call in client.calls if call[0] == "get_transcript"]
    assert transcript_calls[1][1].get("cursor") == "next-1"


def test_fetch_recordings_deduplicates_repeated_pages() -> None:
    row = _file_row("rec-001")
    client = FakeClient(
        pages=[_files_text(*(row for _ in range(60)))],
        notes={"rec-001": [_note_item("요약")]},
        transcripts={"rec-001": _transcript([_segment("전문", start_ms=0)])},
    )
    recordings = fetch_recordings(client, date_from=None, page_size=50)
    assert len(recordings) == 1


def test_fetch_recordings_skips_recording_when_note_fails() -> None:
    client = FakeClient(
        pages=[_files_text(_file_row("rec-001"), _file_row("rec-002"))],
        notes={"rec-001": PlaudMcpError("API error: 500"), "rec-002": [_note_item("요약")]},
        transcripts={
            "rec-001": _transcript([_segment("x", start_ms=0)]),
            "rec-002": _transcript([_segment("y", start_ms=0)]),
        },
    )
    recordings = fetch_recordings(client, date_from=None)
    assert tuple(r.id for r in recordings) == ("rec-002",)


def test_fetch_recordings_skips_recording_when_transcript_fails() -> None:
    client = FakeClient(
        pages=[_files_text(_file_row("rec-001"), _file_row("rec-002"))],
        notes={"rec-001": [_note_item("요약1")], "rec-002": [_note_item("요약2")]},
        transcripts={
            "rec-001": PlaudMcpError("API error: 500"),
            "rec-002": _transcript([_segment("y", start_ms=0)]),
        },
    )
    recordings = fetch_recordings(client, date_from=None)
    assert tuple(r.id for r in recordings) == ("rec-002",)


def test_fetch_recordings_propagates_list_files_failure() -> None:
    class FailingClient:
        def call_tool(
            self, name: str, arguments: JsonObject, timeout: float = 60.0
        ) -> JsonObject:
            raise PlaudMcpError("Not authenticated")

    with pytest.raises(PlaudMcpError):
        _ = fetch_recordings(FailingClient(), date_from=None)


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
    assert "- 사람:: [[김철수]]" in result.bodies["rec-001"]
    assert "- 장소:: 구내식당" in result.bodies["rec-001"]


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
