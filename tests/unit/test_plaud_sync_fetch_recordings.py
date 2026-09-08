from __future__ import annotations

import json
import pytest
from automation.plaud_sync.fetch import fetch_recordings
from automation.plaud_sync.mcp_client import JsonObject, JsonValue, PlaudMcpError


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


