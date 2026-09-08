"""로컬 전사 도구와 환경 설정을 해석한다. 공개 경로는 stt_local이 재수출한다."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import stt_coverage
import stt_media
import stt_window
import stt_window_run

DEFAULT_LANGUAGE: Final = "ko"
DEFAULT_TIMEOUT: Final = 14400.0
_MAX_THREADS: Final = 16
# 문맥 이월로 창 전체가 반복문으로 무너지는 일을 막는다.
DEFAULT_MAX_CONTEXT: Final = "0"
MIN_CONFIGURED_WINDOW_MS: Final = 30_000


@dataclass(frozen=True, slots=True)
class LocalToolchain:
    """네트워크 없이 전사하는 데 필요한 실행 파일과 디코드 설정."""

    binary: Path
    model: Path
    ffmpeg: Path
    ffprobe: Path | None
    threads: int
    language: str
    timeout: float
    allow_incomplete: bool
    prompt: str
    repeat_limit: float
    max_context: str
    window_ms: int
    overlap_ms: int
    decode_flags: tuple[str, ...] = ()


def _threads(env: Mapping[str, str]) -> int:
    raw = env.get("SPEECHTOTEXT_WHISPER_THREADS", "")
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return min(os.cpu_count() or 4, _MAX_THREADS)


def resolve_toolchain(env: Mapping[str, str]) -> LocalToolchain | None:
    """필수 도구나 모델이 없으면 전사기를 만들지 않는다."""
    binary = stt_media.resolve_tool(env.get("SPEECHTOTEXT_WHISPER_BIN", ""), "whisper-cli")
    ffmpeg = stt_media.resolve_ffmpeg(env)
    raw_model = env.get("SPEECHTOTEXT_WHISPER_MODEL", "")
    if binary is None or ffmpeg is None or not raw_model:
        return None
    model = Path(raw_model).expanduser()
    if not model.is_file():
        return None
    raw_timeout = env.get("SPEECHTOTEXT_LOCAL_TIMEOUT", "")
    ffprobe = stt_media.resolve_ffprobe(env, ffmpeg=ffmpeg)
    return LocalToolchain(
        binary=binary,
        model=model,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        allow_incomplete=env.get("SPEECHTOTEXT_ALLOW_INCOMPLETE") == "1",
        threads=_threads(env),
        language=env.get("SPEECHTOTEXT_LANGUAGE") or DEFAULT_LANGUAGE,
        prompt=env.get("SPEECHTOTEXT_PROMPT", ""),
        repeat_limit=_ratio(env.get("SPEECHTOTEXT_MAX_REPEAT", "")),
        max_context=env.get("SPEECHTOTEXT_WHISPER_CONTEXT") or DEFAULT_MAX_CONTEXT,
        window_ms=_whole(
            env, "SPEECHTOTEXT_WINDOW_MS", stt_window.DEFAULT_WINDOW_MS,
            floor=MIN_CONFIGURED_WINDOW_MS,
        ),
        overlap_ms=_whole(env, "SPEECHTOTEXT_WINDOW_OVERLAP_MS", stt_window.DEFAULT_OVERLAP_MS),
        decode_flags=stt_window_run.decode_flags(env),
        timeout=float(raw_timeout) if raw_timeout.replace(".", "", 1).isdigit() else DEFAULT_TIMEOUT,
    )


def _whole(env: Mapping[str, str], name: str, fallback: int, *, floor: int = 0) -> int:
    raw = env.get(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) >= floor else fallback


def _ratio(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        return stt_coverage.DEFAULT_REPEAT_LIMIT
    return value if 0.0 < value <= 1.0 else stt_coverage.DEFAULT_REPEAT_LIMIT
