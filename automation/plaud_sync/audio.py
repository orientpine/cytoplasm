"""Plaud recording audio — presigned-URL parsing and a capped streaming download.

``get_file`` answers with a JSON object *followed by prose* ("Note: source_list …",
2026-09-04 실측), so the object is read with ``raw_decode`` rather than ``loads``.
The presigned S3 URL signs GET only (HEAD → 403), so the size cap is enforced on
``Content-Length`` when present and on the byte stream always; a download that
breaks the cap leaves no file behind. Writes are atomic (temp + replace), which is
what lets an existing non-empty destination count as a complete cache hit.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Protocol
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DEFAULT_MAX_AUDIO_BYTES: Final = 1024 * 1024 * 1024
DEFAULT_TIMEOUT: Final = 120.0
_CHUNK_BYTES: Final = 1 << 20
_USER_AGENT: Final = "autophagy-plaud-sync/1.0"


class AudioError(RuntimeError):
    """The audio for a recording cannot be located or fetched without guessing."""


class AudioTooLargeError(AudioError):
    """The recording itself breaks the size cap — retrying will not change that."""


@dataclass(frozen=True, slots=True)
class AudioSource:
    recording_id: str
    name: str
    created_at: str
    start_at: str
    duration_ms: int
    url: str
    suffix: str


class ResponseLike(Protocol):
    @property
    def headers(self) -> object: ...

    def read(self, size: int) -> bytes: ...


Opener = Callable[[str, float], AbstractContextManager[ResponseLike]]


def _text_field(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def parse_source(text: str, recording_id: str) -> AudioSource:
    try:
        payload, _ = json.JSONDecoder().raw_decode(text.lstrip())
    except json.JSONDecodeError as error:
        raise AudioError("get_file 응답이 JSON 으로 시작하지 않는다") from error
    if not isinstance(payload, dict):
        raise AudioError("get_file 응답이 객체가 아니다")
    if payload.get("id") != recording_id:
        raise AudioError("get_file 응답의 id 가 요청한 녹음과 다르다")
    url = payload.get("presigned_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise AudioError("get_file 응답에 https presigned_url 이 없다")
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    if not suffix:
        raise AudioError("오디오 URL 에 파일 확장자가 없어 형식을 정할 수 없다")
    duration = payload.get("duration")
    duration_ms = (
        int(duration)
        if isinstance(duration, (int, float)) and not isinstance(duration, bool)
        else 0
    )
    return AudioSource(
        recording_id=recording_id,
        name=_text_field(payload, "name"),
        created_at=_text_field(payload, "created_at"),
        start_at=_text_field(payload, "start_at"),
        duration_ms=duration_ms,
        url=url,
        suffix=suffix,
    )


def open_url(url: str, timeout: float) -> AbstractContextManager[ResponseLike]:
    return urlopen(Request(url, headers={"User-Agent": _USER_AGENT}), timeout=timeout)  # noqa: S310 - https enforced by parse_source


def _declared_length(response: ResponseLike) -> int | None:
    headers = response.headers
    getter = getattr(headers, "get", None)
    raw = getter("Content-Length") if callable(getter) else None
    return int(raw) if isinstance(raw, str) and raw.isdigit() else None


def _stream(response: ResponseLike, handle: object, max_bytes: int) -> int:
    write = getattr(handle, "write")
    total = 0
    while chunk := response.read(_CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            raise AudioTooLargeError(f"오디오가 상한 {max_bytes} B 를 넘어 다운로드를 중단했다")
        write(chunk)
    return total


def download(
    source: AudioSource,
    dest: Path,
    *,
    max_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
    opener: Opener = open_url,
    timeout: float = DEFAULT_TIMEOUT,
) -> Path:
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    temporary: Path | None = None
    try:
        dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with opener(source.url, timeout) as response:
            declared = _declared_length(response)
            if declared is not None and declared > max_bytes:
                raise AudioTooLargeError(f"오디오 {declared} B 가 상한 {max_bytes} B 를 넘는다")
            with tempfile.NamedTemporaryFile(
                dir=dest.parent, prefix=f".{dest.name}.", delete=False
            ) as handle:
                temporary = Path(handle.name)
                total = _stream(response, handle, max_bytes)
                handle.flush()
                os.fsync(handle.fileno())
        if total == 0:
            raise AudioError("오디오 본문이 비어 있다")
        os.chmod(temporary, 0o600)
        os.replace(temporary, dest)
        temporary = None
    except (OSError, ValueError) as error:
        raise AudioError(f"오디오 다운로드 실패: {type(error).__name__}: {error}"[:200]) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return dest
