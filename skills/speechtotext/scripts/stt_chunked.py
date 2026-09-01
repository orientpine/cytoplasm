"""Transcribe a recording that exceeds the API upload cap, without losing a second.

The API refuses anything past 25MiB, which a 2-hour meeting passes easily. The
answer is not "ask the owner to split the file" — it is to window the audio
here, with a deliberate overlap so no word falls into a seam, transcribe each
window in order, and stitch the results by removing only text that provably
repeats across the seam. Text that does not match is kept on both sides:
duplicated words are a nuisance, dropped words are the failure we are avoiding.

Because the windows are computed from the real duration and tile it completely,
the same coverage verdict the local path produces applies here — the plan
itself is the proof that every span was sent.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final, TypeAlias

import stt_audio
import stt_client
import stt_coverage

DEFAULT_WINDOW_MS: Final = 900_000
DEFAULT_OVERLAP_MS: Final = 10_000
_MIN_SEAM_TOKENS: Final = 2
_MAX_SEAM_TOKENS: Final = 80
_EXTRACT_TIMEOUT: Final = 1800.0
_CHUNK_BITRATE: Final = "32k"

Window: TypeAlias = tuple[int, int]
TranscribeWindow: TypeAlias = Callable[[Path, int], str]

WINDOW_EMPTY_NOTICE: Final = (
    "전사 누락 의심: {at:.0f}분 지점 구간의 전사 결과가 비어 있습니다. "
    "일부만 담긴 전사본을 회의록으로 넘기지 않고 중단합니다."
)
NO_DURATION_NOTICE: Final = (
    "녹음 길이를 확인하지 못해 분할 전사를 계획할 수 없습니다(ffprobe 필요). "
    "누락 위험이 있는 채로 진행하지 않습니다."
)


def plan_windows(
    duration_ms: int,
    *,
    window_ms: int = DEFAULT_WINDOW_MS,
    overlap_ms: int = DEFAULT_OVERLAP_MS,
) -> tuple[Window, ...]:
    """Overlapping windows whose union is exactly the whole recording."""
    duration = max(int(duration_ms), 0)
    if duration <= 0:
        return ()
    window = max(int(window_ms), 1_000)
    if duration <= window:
        return ((0, duration),)
    overlap = min(max(int(overlap_ms), 0), window // 2)
    step = window - overlap
    planned: list[Window] = []
    start = 0
    while start < duration:
        end = min(start + window, duration)
        planned.append((start, end))
        if end >= duration:
            break
        start += step
    return tuple(planned)


def _join(left: str, right: str) -> str:
    left_tokens, right_tokens = left.split(), right.split()
    limit = min(len(left_tokens), len(right_tokens), _MAX_SEAM_TOKENS)
    for size in range(limit, _MIN_SEAM_TOKENS - 1, -1):
        if left_tokens[-size:] == right_tokens[:size]:
            return " ".join([*left_tokens, *right_tokens[size:]])
    return " ".join([*left_tokens, *right_tokens])


def stitch(parts: Sequence[str]) -> str:
    """Concatenate window transcripts, removing only a seam that provably repeats."""
    joined = ""
    for part in parts:
        text = " ".join(part.split())
        if not text:
            continue
        joined = _join(joined, text) if joined else text
    return joined


def extract_window(
    source: Path, dest: Path, window: Window, *, ffmpeg: Path, timeout: float = _EXTRACT_TIMEOUT
) -> None:
    """Cut one window to mono 16 kHz mp3 — small enough to upload, faithful enough to read."""
    start, end = window
    argv = [
        str(ffmpeg), "-nostdin", "-y",
        "-ss", f"{start / 1000:.3f}",
        "-i", str(source),
        "-t", f"{(end - start) / 1000:.3f}",
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-b:a", _CHUNK_BITRATE,
        str(dest),
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - argv built from a resolved executable
            argv, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as failure:
        raise stt_client.SttError(f"구간 추출 실패: {type(failure).__name__}") from None
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()[-200:]
        raise stt_client.SttError(f"구간 추출 실패 rc={completed.returncode}: {detail}")


def transcribe_long(
    audio: stt_audio.CheckedAudio,
    *,
    duration_ms: int,
    ffmpeg: Path,
    transcribe_window: TranscribeWindow,
    model: str,
    window_ms: int = DEFAULT_WINDOW_MS,
    overlap_ms: int = DEFAULT_OVERLAP_MS,
) -> stt_client.Transcription:
    """Window → transcribe → stitch, refusing rather than returning a partial transcript."""
    windows = plan_windows(duration_ms, window_ms=window_ms, overlap_ms=overlap_ms)
    if not windows:
        raise stt_audio.TranscriptionRefused(NO_DURATION_NOTICE, exit_code=8)
    texts: list[str] = []
    with tempfile.TemporaryDirectory(prefix="stt-window-") as workdir:
        for index, window in enumerate(windows):
            chunk = Path(workdir) / f"window-{index:05d}.mp3"
            extract_window(audio.path, chunk, window, ffmpeg=ffmpeg)
            text = transcribe_window(chunk, index).strip()
            if not text:
                raise stt_audio.TranscriptionRefused(
                    WINDOW_EMPTY_NOTICE.format(at=window[0] / 60_000), exit_code=5
                )
            texts.append(text)
    return stt_client.Transcription(
        text=stitch(texts),
        model=model,
        endpoint="api-chunked",
        coverage=stt_coverage.assess(windows, duration_ms),
    )
