"""Audio input policy for the speechtotext skill (deterministic, no I/O of content).

Runs BEFORE any credential is read or any byte leaves the node: size gate first,
then an extension allowlist that mirrors what the transcription API accepts.
Every refusal carries a Korean owner-facing notice and a stable exit code, the
same contract `meeting_extract.ExtractionRefused` uses so the CLI can chain both.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

# The 25MiB ceiling is the transcription API's upload limit, not a property of
# audio: a local whisper.cpp run reads a 3-hour recording straight off disk.
MAX_API_AUDIO_BYTES: Final = 25 * 1024 * 1024
MAX_LOCAL_AUDIO_BYTES: Final = 8 * 1024 * 1024 * 1024
MAX_AUDIO_BYTES: Final = MAX_API_AUDIO_BYTES

SUPPORTED_SUFFIXES: Final = frozenset(
    {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}
)

_MIME_BY_SUFFIX: Final[dict[str, str]] = {
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}

SIZE_EXCEEDED_NOTICE: Final = (
    "크기 초과: 이 전사 경로가 허용하는 크기를 넘었습니다(외부 API 상한 25MiB). "
    "로컬 전사(whisper.cpp)를 설정하면 2시간이 넘는 녹취도 나누지 않고 그대로 처리합니다."
)
UNSUPPORTED_NOTICE: Final = (
    "지원하지 않는 형식입니다. flac/mp3/mp4/mpeg/mpga/m4a/wav/webm 음성만 전사합니다."
)
MISSING_NOTICE: Final = "음성 파일을 찾을 수 없습니다. 경로를 확인해 주세요."
EMPTY_TRANSCRIPT_NOTICE: Final = (
    "전사 결과가 비어 있습니다: 음성이 들어 있는 파일인지 확인해 주세요. 내용은 추측하지 않습니다."
)


def limit_for(backend: str) -> int:
    """Size ceiling for a backend — the API cap only binds the API path."""
    return MAX_LOCAL_AUDIO_BYTES if backend == "local" else MAX_API_AUDIO_BYTES


class TranscriptionRefused(Exception):
    """Deterministic refusal with a user-facing Korean notice and exit code."""

    def __init__(self, notice: str, exit_code: int) -> None:
        super().__init__(notice)
        self.notice = notice
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class CheckedAudio:
    """An audio file that passed the size gate and the extension allowlist."""

    path: Path
    suffix: str
    size_bytes: int
    mime: str


def check_audio(path: Path, *, max_bytes: int | None = None) -> CheckedAudio:
    """Size-gate then allowlist ``path``; never reads a content byte."""
    limit = MAX_AUDIO_BYTES if max_bytes is None else max_bytes
    try:
        size = path.stat().st_size
    except OSError as error:
        raise TranscriptionRefused(MISSING_NOTICE, exit_code=5) from error
    if size > limit:
        raise TranscriptionRefused(SIZE_EXCEEDED_NOTICE, exit_code=3)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise TranscriptionRefused(UNSUPPORTED_NOTICE, exit_code=5)
    return CheckedAudio(
        path=path, suffix=suffix, size_bytes=size, mime=_MIME_BY_SUFFIX[suffix]
    )
