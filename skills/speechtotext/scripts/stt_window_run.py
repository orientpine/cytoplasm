"""Running the windows: one whisper.cpp process per window, and no shared fate.

Split from `stt_local.py`, which owns the *decisions* (toolchain, refusal, coverage),
and from `stt_window_store.py`, which owns *where the results live*. What is left here
is the machinery of a single window: build the argv, run it under a budget derived from
that window's own length, read the payload without letting a byte be fatal, and hand
back either its segments or the reason it has none.

Two rules decide everything:

* **A window fails alone.** Non-zero rc, timeout, unreadable payload, JSON that stays
  invalid after the replacement decode, or a repetition collapse inside that window —
  each costs that window and nothing else. The raw payload is copied out for forensics,
  one machine-readable line goes to stderr, and a gap marker takes its place.
* **Nothing valid is decoded twice, nothing invalid is trusted.** A window whose JSON
  already parsed is reused from the store; a cached payload that does not parse is a
  cache miss, not a failure — whisper simply runs that window again.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

import stt_coverage
import stt_window
import stt_window_store

#: A window may take this many times its own length before it is called a timeout.
BUDGET_FACTOR: Final = 4.0
MIN_BUDGET: Final = 900.0
QUARANTINED: Final = "WHISPER-WINDOW-QUARANTINED"


class Toolchain(Protocol):
    """The parts of `stt_local.LocalToolchain` a window run actually needs."""

    binary: Path
    model: Path
    threads: int
    language: str
    timeout: float
    allow_incomplete: bool
    max_context: str
    repeat_limit: float


@dataclass(frozen=True, slots=True)
class WindowReport:
    """What the run produced, and which windows are only a marker."""

    results: tuple[stt_window.WindowResult, ...]
    quarantined: tuple[int, ...]


def argv_for(
    wav: Path,
    window: stt_window.Window,
    toolchain: Toolchain,
    *,
    out: Path,
    prompt: str,
    sliced: bool,
) -> list[str]:
    """The whisper.cpp command for one window of the already-normalized wav.

    ``-ot``/``-d`` cut the window inside the decoder, so the audio is read once and no
    slice file is ever written. Verified on the node: with ``-ot`` the offsets whisper
    reports are the recording's own, so nothing has to be shifted back afterwards.
    A single window covering the whole file keeps exactly the argv it always had.
    """
    return [
        str(toolchain.binary), "-m", str(toolchain.model), "-f", str(wav),
        "-l", toolchain.language, "-t", str(toolchain.threads),
        # -ojf keeps per-segment timings (the completeness evidence).
        # Deliberately absent: -nf (disables the temperature fallback that
        # rescues a failed window), --vad (trims quiet Korean speech) and
        # -mc (truncates carried context) — each one can drop real speech.
        "-ojf", "-of", str(out), "-np",
        *(("-ot", str(window.start_ms), "-d", str(window.length_ms)) if sliced else ()),
        *(("--prompt", prompt) if prompt else ()),
        *(("-mc", toolchain.max_context) if toolchain.max_context else ()),
    ]


def budget(window: stt_window.Window, toolchain: Toolchain, deadline: float) -> float:
    """Seconds this window may take: its own length, under the overall ceiling.

    One unbounded pass over the whole file (an unreadable duration) keeps the whole
    ceiling, which is what that single run always had.
    """
    remaining = max(deadline - time.monotonic(), 0.0)
    if window.length_ms <= 0:
        return min(toolchain.timeout, remaining)
    derived = max(window.length_ms / 1000 * BUDGET_FACTOR, MIN_BUDGET)
    return min(derived, toolchain.timeout, remaining)


def _run_window(
    wav: Path,
    window: stt_window.Window,
    toolchain: Toolchain,
    *,
    workdir: Path,
    prompt: str,
    sliced: bool,
    deadline: float,
) -> tuple[bytes | None, str]:
    """Transcribe one window; returns its raw payload, or the reason it has none."""
    allowance = budget(window, toolchain, deadline)
    if allowance <= 0:
        return None, "budget"
    out = workdir / f"window-{window.index:05d}"
    argv = argv_for(wav, window, toolchain, out=out, prompt=prompt, sliced=sliced)
    try:
        completed = subprocess.run(  # noqa: S603 - argv is built from resolved executables
            argv, capture_output=True, timeout=allowance, check=False
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as failure:
        return None, type(failure).__name__
    if completed.returncode != 0:
        return None, f"rc={completed.returncode}"
    try:
        return out.with_suffix(".json").read_bytes(), ""
    except OSError:
        return None, "unreadable"


def accept(
    raw: bytes,
    window: stt_window.Window,
    toolchain: Toolchain,
    *,
    repetition: bool = True,
) -> tuple[tuple[stt_window.Segment, ...] | None, str]:
    """Read one window's payload; returns its segments, or why they cannot be trusted.

    ``repetition`` is off for a single unsliced window: there the check would be the
    whole-transcript one `stt_local` already runs, and that one refuses with the notice
    telling the owner what collapsed and how to override it — a marker would say less.
    """
    text, replaced = stt_window.decode_payload(raw)
    if replaced:
        print(f"WHISPER-WINDOW-REPAIRED index={window.index} replaced={replaced}", file=sys.stderr)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, "invalid-json"
    segments = data.get("transcription") if isinstance(data, dict) else None
    if not isinstance(segments, list):
        return None, "no-transcription"
    kept = tuple(segment for segment in segments if isinstance(segment, Mapping))
    if not repetition or toolchain.allow_incomplete:
        return kept, ""
    ratio, _phrase = stt_coverage.collapsed(stt_window.text_of(kept), limit=toolchain.repeat_limit)
    return (None, f"repetition={ratio:.2f}") if ratio else (kept, "")


def run_windows(
    wav: Path,
    windows: Sequence[stt_window.Window],
    toolchain: Toolchain,
    *,
    workdir: Path,
    prompt: str,
    store: stt_window_store.WindowStore,
) -> WindowReport:
    """One whisper.cpp process per window; a window that fails takes only itself down."""
    deadline = time.monotonic() + toolchain.timeout
    sliced = len(windows) > 1
    results: list[stt_window.WindowResult] = []
    quarantined: list[int] = []
    for position, window in enumerate(windows):
        cached = store.load(window)
        segments = None if cached is None else accept(cached, window, toolchain,
                                                      repetition=sliced)[0]
        if segments is not None:
            print(f"WHISPER-WINDOW-CACHED index={window.index}", file=sys.stderr)
            results.append(stt_window.WindowResult(window, segments))
            continue
        raw, reason = _run_window(
            wav, window, toolchain, workdir=workdir, prompt=prompt,
            sliced=sliced, deadline=deadline,
        )
        if raw is not None:
            segments, reason = accept(raw, window, toolchain, repetition=sliced)
        if segments is None or raw is None:
            print(f"{QUARANTINED} index={window.index} reason={reason}", file=sys.stderr)
            if raw is not None:
                store.quarantine(window, raw)
            quarantined.append(window.index)
            results.append(stt_window.gap_result(window, until=_owns_until(windows, position)))
            continue
        store.save(window, raw)
        results.append(stt_window.WindowResult(window, segments))
    return WindowReport(results=tuple(results), quarantined=tuple(quarantined))


def _owns_until(windows: Sequence[stt_window.Window], position: int) -> int:
    """Where this window's own minutes end: the next window's start, or the recording's.

    Windows overlap, and `stt_window.merge` gives the overlap to the *next* window —
    which normally transcribed it. A marker that named the overlap too would send the
    owner back to minutes the document already carries.
    """
    window = windows[position]
    if position + 1 >= len(windows):
        return window.end_ms
    return min(windows[position + 1].start_ms, window.end_ms)
