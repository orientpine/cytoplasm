"""Resume cache: a different transcriber build must not reuse another build's windows.

Kept apart from ``test_stt_window.py`` per ``tests/AGENTS.md`` — new cases go in a new
file rather than growing one whose replay a settlement record could pin.

``cache_key`` covered audio, model name and window plan, but not the whisper binary. A
window decoded by an older or broken build therefore stayed authoritative forever: fix
the transcriber, re-run the same recording, and the cache hands back the old result.

Measured 2026-09-05 in the speechtotext deploy scenario — case [6] transcribed a 2-hour
recording that stopped at 12 minutes, case [7] re-ran the same audio with a build that
covered it end to end, and window 0 came back from cache (``WHISPER-WINDOW-CACHED
index=0``) leaving a 3-minute hole. ``SANDBOX-BLOCK: scenario failed under dummy
secrets`` blocked every speechtotext deploy, so v1.2.0 and v1.2.1 both left the mount
stale. Narrowing the key can only reduce reuse, never widen it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "speechtotext" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import stt_window  # noqa: E402
import stt_window_store  # noqa: E402


def _windows() -> tuple[stt_window.Window, ...]:
    return stt_window.plan_windows(240_000, window_ms=100_000, overlap_ms=20_000)


def _store_key(tmp_path: Path, *, tool_bytes: bytes) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"audio-bytes")
    tool = tmp_path / "whisper-cli"
    tool.write_bytes(tool_bytes)
    return stt_window_store.resolve_store(
        {"SPEECHTOTEXT_WINDOW_CACHE": str(tmp_path / "cache")},
        audio=audio,
        model=tmp_path / "ggml-large-v3-turbo-q5_0.bin",
        windows=_windows(),
        tool=tool,
    ).key


def test_the_same_transcriber_build_keeps_one_resume_key(tmp_path: Path) -> None:
    first = _store_key(tmp_path / "one", tool_bytes=b"whisper-build-a")
    second = _store_key(tmp_path / "two", tool_bytes=b"whisper-build-a")

    assert first == second


def test_a_changed_transcriber_build_gets_a_different_resume_key(tmp_path: Path) -> None:
    old = _store_key(tmp_path / "old", tool_bytes=b"whisper-build-a")
    new = _store_key(tmp_path / "new", tool_bytes=b"whisper-build-b-fixed")

    assert old != new


def test_the_key_still_separates_audio_model_and_plan(tmp_path: Path) -> None:
    base = stt_window.cache_key(
        audio_sha256="abc", model="ggml", tool="tool-a", windows=_windows()
    )

    assert base == stt_window.cache_key(
        audio_sha256="abc", model="ggml", tool="tool-a", windows=_windows()
    )
    assert base != stt_window.cache_key(
        audio_sha256="def", model="ggml", tool="tool-a", windows=_windows()
    )
    assert base != stt_window.cache_key(
        audio_sha256="abc", model="other", tool="tool-a", windows=_windows()
    )
    assert base != stt_window.cache_key(
        audio_sha256="abc", model="ggml", tool="tool-b", windows=_windows()
    )
    assert base != stt_window.cache_key(
        audio_sha256="abc",
        model="ggml",
        tool="tool-a",
        windows=stt_window.plan_windows(600_000, window_ms=100_000, overlap_ms=20_000),
    )
