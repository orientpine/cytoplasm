"""``automation.plaud_sync.audio`` — the presigned Plaud audio URL and its capped download.

Separate from the fetch parser tests: ``get_file`` is the one plaud-mcp payload whose
text is JSON *followed by prose* (2026-09-04 실측: the server appends a "Note: …"
paragraph after the object), and the download is the only place plaud_sync pulls a
binary from S3 — HEAD on the presigned URL is 403, so the size cap can only be
enforced on the GET stream.
"""

from __future__ import annotations

import io
import json
import stat
from pathlib import Path
from typing import Final

import pytest

from automation.plaud_sync.audio import AudioError, AudioSource, download, parse_source

_URL: Final = (
    "https://apne1-prod-plaud-bucket.s3-accelerate.amazonaws.com/files/rec-001.mp3"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=86400&X-Amz-Signature=abc"
)
_PAYLOAD: Final = {
    "id": "rec-001",
    "name": "09-02 직장 동료들의 일상 대화",
    "created_at": "2026-09-02T05:26:44",
    "serial_number": "8800B50228039889",
    "start_at": "2026-09-02T02:19:04",
    "duration": 7037000,
    "presigned_url": _URL,
    "source_list": [],
    "note_list": [],
}
_TAIL: Final = "\n\nNote: source_list `transaction_polish` returned an empty `data_content` — the …"


def _source(**overrides: object) -> AudioSource:
    return parse_source(json.dumps({**_PAYLOAD, **overrides}) + _TAIL, "rec-001")


class _Response:
    def __init__(self, body: bytes, length: str | None) -> None:
        self._buffer = io.BytesIO(body)
        self.headers = {"Content-Length": length} if length is not None else {}

    def read(self, size: int) -> bytes:
        return self._buffer.read(size)

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exception: object) -> bool:
        return False


def _opener(body: bytes, *, length: str | None = None, calls: list[str] | None = None):
    def open_(url: str, timeout: float) -> _Response:
        if calls is not None:
            calls.append(url)
        return _Response(body, length)

    return open_


def test_parse_source_when_prose_follows_the_json_then_url_suffix_and_metadata_are_read() -> None:
    source = _source()
    assert source == AudioSource(
        recording_id="rec-001",
        name="09-02 직장 동료들의 일상 대화",
        created_at="2026-09-02T05:26:44",
        start_at="2026-09-02T02:19:04",
        duration_ms=7037000,
        url=_URL,
        suffix=".mp3",
    )


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"id": "rec-999"}, "id"),
        ({"presigned_url": "https://example.invalid/files/rec-001"}, "확장자"),
        ({"presigned_url": ""}, "presigned_url"),
        ({"presigned_url": "http://example.invalid/rec-001.mp3"}, "presigned_url"),
    ],
)
def test_parse_source_when_payload_is_unusable_then_refuses(
    override: dict[str, object], reason: str
) -> None:
    with pytest.raises(AudioError, match=reason):
        _source(**override)


def test_parse_source_when_text_is_not_json_then_refuses() -> None:
    with pytest.raises(AudioError, match="JSON"):
        parse_source("Not authenticated", "rec-001")


def test_download_when_stream_fits_then_writes_owner_only_file_atomically(tmp_path: Path) -> None:
    dest = tmp_path / "audio" / "rec-001.mp3"
    calls: list[str] = []
    result = download(
        _source(), dest, max_bytes=64, opener=_opener(b"x" * 40, length="40", calls=calls)
    )
    assert result == dest
    assert dest.read_bytes() == b"x" * 40
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert calls == [_URL]
    assert [p.name for p in dest.parent.iterdir()] == ["rec-001.mp3"], "no temp file may survive"


def test_download_when_declared_length_exceeds_cap_then_refuses_before_reading(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "rec-001.mp3"
    with pytest.raises(AudioError, match="상한"):
        download(_source(), dest, max_bytes=64, opener=_opener(b"x" * 10, length="65"))
    assert not dest.exists()


def test_download_when_stream_exceeds_cap_then_refuses_and_leaves_nothing(tmp_path: Path) -> None:
    dest = tmp_path / "rec-001.mp3"
    with pytest.raises(AudioError, match="상한"):
        download(_source(), dest, max_bytes=64, opener=_opener(b"x" * 65))
    assert list(tmp_path.iterdir()) == []


def test_download_when_body_is_empty_then_refuses(tmp_path: Path) -> None:
    with pytest.raises(AudioError, match="비어"):
        download(_source(), tmp_path / "rec-001.mp3", max_bytes=64, opener=_opener(b""))
    assert list(tmp_path.iterdir()) == []


def test_download_when_file_is_already_cached_then_does_not_open_the_url(tmp_path: Path) -> None:
    dest = tmp_path / "rec-001.mp3"
    dest.write_bytes(b"cached")
    calls: list[str] = []
    assert download(_source(), dest, max_bytes=64, opener=_opener(b"new", calls=calls)) == dest
    assert dest.read_bytes() == b"cached"
    assert calls == []


def test_download_when_opener_raises_oserror_then_wraps_as_audio_error(tmp_path: Path) -> None:
    def broken(url: str, timeout: float) -> _Response:
        raise OSError("connection reset")

    with pytest.raises(AudioError, match="다운로드 실패"):
        download(_source(), tmp_path / "rec-001.mp3", max_bytes=64, opener=broken)
