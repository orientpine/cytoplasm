"""Media facts read from the file itself, never assumed.

Only one fact is needed and it decides whether a transcript can be trusted as
complete: how long the recording actually is. ``ffprobe`` answers in one
machine-readable call; when it cannot, the answer is ``None`` rather than a
guess, and the caller reports coverage as unknown instead of claiming success.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import wave
from collections.abc import Mapping
from pathlib import Path
from typing import Final

PROBE_TIMEOUT: Final = 120.0


def wav_duration_ms(wav: Path) -> int:
    """How long a wav is, read from its own header (0 when it cannot be read).

    Used to plan the transcription windows of the normalized 16 kHz wav the skill
    just wrote itself: the header already knows, so no second probe process runs and
    no length is ever guessed.
    """
    try:
        with wave.open(str(wav), "rb") as handle:
            frames, rate = handle.getnframes(), handle.getframerate()
    except (OSError, wave.Error, EOFError):
        return 0
    return round(frames * 1000 / rate) if rate > 0 else 0


def probe_duration_ms(path: Path, *, ffprobe: Path, timeout: float = PROBE_TIMEOUT) -> int | None:
    """Duration in milliseconds, or ``None`` when the media cannot be probed."""
    argv = [
        str(ffprobe), "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - argv built from a resolved executable
            argv, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
        seconds = float(payload["format"]["duration"])
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    return round(seconds * 1000)


def resolve_tool(explicit: str, fallback: str) -> Path | None:
    """An executable named explicitly, else found on PATH, else ``None``."""
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None
    found = shutil.which(fallback)
    return Path(found) if found else None


def resolve_ffmpeg(env: Mapping[str, str]) -> Path | None:
    return resolve_tool(env.get("SPEECHTOTEXT_FFMPEG_BIN", ""), "ffmpeg")


def resolve_ffprobe(env: Mapping[str, str], *, ffmpeg: Path | None = None) -> Path | None:
    """ffprobe by name, else the one shipped beside the resolved ffmpeg."""
    found = resolve_tool(env.get("SPEECHTOTEXT_FFPROBE_BIN", ""), "ffprobe")
    if found is not None or ffmpeg is None:
        return found
    sibling = ffmpeg.with_name("ffprobe")
    return sibling if sibling.is_file() and os.access(sibling, os.X_OK) else None
