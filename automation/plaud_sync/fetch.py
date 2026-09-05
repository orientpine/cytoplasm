"""Defensive parsing of plaud-mcp tool payloads into lifelog recordings.

Shapes are measured against the deployed plaud-mcp server (2026-09-04), not the
docs: ``get_note`` returns a top-level list of items keyed ``data_content`` /
``data_title`` / ``data_error_code``; ``get_transcript`` returns ``segments``
(``start_time`` int ms, ``content``, ``speaker``) with ``next_cursor``. Its supported
blocks are ``transaction``, ``outline``, ``transaction_polish`` and ``mark_memo``;
try those in server order, then legacy ``default``. An empty recording returns ``[]``.
The list/dict/plain-text fallbacks keep older or alternate shapes working.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, Protocol

from .mcp_client import JsonObject, JsonValue, PlaudMcpError, text_content
from .note import LifelogRecording

_MAX_LIST_PAGES: Final = 10
_MAX_TRANSCRIPT_PAGES: Final = 50
_NOTE_LIST_KEYS: Final = ("note_list", "data")
_NOTE_CONTENT_KEYS: Final = ("data_content", "content", "markdown", "text", "note")
_NOTE_TITLE_KEYS: Final = ("data_title", "data_tab_name", "title")
_SEGMENT_LIST_KEYS: Final = ("segments", "marks", "source_list", "data")
_SEGMENT_TEXT_KEYS: Final = ("content", "text")
_SEGMENT_TIME_KEYS: Final = ("start_time", "start", "timestamp")
_TRANSCRIPT_BLOCKS: Final = ("transaction", "outline", "transaction_polish", "mark_memo", "default")

_UNSET: Final = object()


class McpToolClient(Protocol):
    """What fetch needs from PlaudMcpClient — the same signature, so the real client satisfies it."""

    def call_tool(
        self, name: str, arguments: dict[str, JsonValue], timeout: float = 60.0
    ) -> JsonObject: ...


@dataclass(frozen=True, slots=True)
class CloudTranscript:
    text: str
    block: str = ""

    @property
    def source_label(self) -> str:
        return f"PLAUD 클라우드 전사({self.block} 블록)" if self.block else "PLAUD 클라우드 전사"


@dataclass(frozen=True, slots=True)
class _FileStub:
    id: str
    name: str
    created_at: str
    start_at: str
    duration_ms: int


def _loads(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _UNSET


def _string_field(item: dict[str, object], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _find_rows(payload: object) -> list[dict[str, object]] | None:
    if isinstance(payload, list):
        rows = [
            {str(key): value for key, value in row.items()}
            for row in payload
            if isinstance(row, dict) and "id" in row
        ]
        return rows or None
    if isinstance(payload, dict):
        for value in payload.values():
            rows = _find_rows(value)
            if rows:
                return rows
    return None


def _optional_string(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


def _stub(row: dict[str, object]) -> _FileStub | None:
    recording_id = row.get("id")
    if not isinstance(recording_id, str) or not recording_id:
        return None
    duration = row.get("duration")
    duration_ms = (
        int(duration)
        if isinstance(duration, (int, float)) and not isinstance(duration, bool)
        else 0
    )
    return _FileStub(
        id=recording_id,
        name=_optional_string(row, "name"),
        created_at=_optional_string(row, "created_at"),
        start_at=_optional_string(row, "start_at"),
        duration_ms=duration_ms,
    )


def _list_stubs(
    client: McpToolClient, date_from: str | None, page_size: int
) -> tuple[_FileStub, ...]:
    seen: dict[str, _FileStub] = {}
    for page in range(1, _MAX_LIST_PAGES + 1):
        arguments: dict[str, JsonValue] = {"page": page, "page_size": page_size}
        if date_from:
            arguments["date_from"] = date_from
        text = text_content(client.call_tool("list_files", arguments))
        rows = _find_rows(_loads(text)) or []
        added = 0
        for row in rows:
            stub = _stub(row)
            if stub is not None and stub.id not in seen:
                seen[stub.id] = stub
                added += 1
        if len(rows) < page_size or added == 0:
            break
    return tuple(seen.values())


def _note_items(payload: object) -> list[object] | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _NOTE_LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def _summary_text(text: str) -> str:
    payload = _loads(text)
    if payload is _UNSET:
        return text.strip()
    if isinstance(payload, str):
        return payload.strip()
    items = _note_items(payload)
    if items is None:
        return ""
    dict_items = [item for item in items if isinstance(item, dict)]
    labelled = len(dict_items) > 1
    parts: list[str] = []
    for item in items:
        if isinstance(item, str):
            if item.strip():
                parts.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        content = _string_field(item, _NOTE_CONTENT_KEYS)
        if not content:
            continue
        title = _string_field(item, _NOTE_TITLE_KEYS) if labelled else ""
        parts.append(f"### {title}\n{content}" if title else content)
    return "\n\n".join(parts)


def fetch_summary(client: McpToolClient, file_id: str) -> str:
    return _summary_text(text_content(client.call_tool("get_note", {"file_id": file_id})))


def _segment_list(payload: object) -> list[object] | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _SEGMENT_LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return None


def _format_timestamp(milliseconds: float) -> str:
    seconds = int(milliseconds) // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _segment_line(segment: object) -> str:
    if isinstance(segment, str):
        return segment.strip()
    if not isinstance(segment, dict):
        return ""
    text = _string_field(segment, _SEGMENT_TEXT_KEYS)
    if not text:
        return ""
    started = ""
    for key in _SEGMENT_TIME_KEYS:
        value = segment.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            started = _format_timestamp(value)
            break
        if isinstance(value, str) and value.strip():
            started = value.strip()
            break
    speaker = segment.get("speaker")
    prefix = [part for part in (started, speaker if isinstance(speaker, str) else "") if part]
    return f"[{' · '.join(prefix)}] {text}" if prefix else text


def _next_cursor(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidate = payload.get("next_cursor") or payload.get("cursor")
    return candidate if isinstance(candidate, str) and candidate else None


def _is_empty_block_message(text: str) -> bool:
    return (text.startswith('Block "') and (
        "not available for this recording" in text or "has no content for this recording yet" in text
    )) or text.startswith("This recording has a highlights block, but it is empty")


def _transcript_block_text(client: McpToolClient, file_id: str, block: str) -> str:
    parts: list[str] = []
    cursor: str | None = None
    for _ in range(_MAX_TRANSCRIPT_PAGES):
        arguments: dict[str, JsonValue] = {"file_id": file_id, "block": block}
        if cursor:
            arguments["cursor"] = cursor
        text = text_content(client.call_tool("get_transcript", arguments))
        payload = _loads(text)
        if payload is _UNSET:
            plain = text.strip()
            if plain and not _is_empty_block_message(plain):
                parts.append(plain)
            break
        segments = _segment_list(payload)
        if segments is None:
            break
        parts.extend(line for line in map(_segment_line, segments) if line)
        cursor = _next_cursor(payload)
        if cursor is None:
            break
    return "\n".join(parts)


def fetch_transcript(client: McpToolClient, file_id: str) -> CloudTranscript:
    last_error: PlaudMcpError | None = None
    answered = False
    for block in _TRANSCRIPT_BLOCKS:
        try:
            text = _transcript_block_text(client, file_id, block)
        except PlaudMcpError as error:
            last_error = error
            continue
        answered = True
        if text:
            return CloudTranscript(text, block)
    if not answered and last_error is not None:
        raise last_error
    return CloudTranscript("")


def fetch_recordings(
    client: McpToolClient, *, date_from: str | None, page_size: int = 50
) -> tuple[LifelogRecording, ...]:
    recordings: list[LifelogRecording] = []
    for stub in _list_stubs(client, date_from, page_size):
        try:
            summary = fetch_summary(client, stub.id)
            transcript = fetch_transcript(client, stub.id)
        except PlaudMcpError:
            continue
        recordings.append(
            LifelogRecording(
                id=stub.id,
                name=stub.name,
                created_at=stub.created_at,
                start_at=stub.start_at,
                duration_ms=stub.duration_ms,
                summary_markdown=summary,
                transcript_text=transcript.text,
                transcript_source=transcript.source_label if transcript.text else "",
            )
        )
    return tuple(recordings)
