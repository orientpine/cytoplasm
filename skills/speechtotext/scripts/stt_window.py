"""Windowed transcription arithmetic: plan the cuts, decode the bytes, merge the seams.

A 2-hour recording used to be one whisper.cpp process and one JSON read at the very
end. On 2026-09-04 that shape cost the owner two hours of speech twice over: the
decoder wrote incomplete UTF-8 Korean bytes into its own `-ojf` payload, `read_text`
raised `UnicodeDecodeError` — which is not a `JSONDecodeError`, so it walked straight
past the handler — and every word of the recording went with it.

Nothing about a long decode requires that shape. Audio is a timeline, and a timeline
can be cut into windows that fail independently. This module owns that arithmetic and
only that: no subprocess, no file write, no clock. It plans windows from a duration,
decodes payload bytes so that no byte is ever fatal, merges per-window segments onto
one global timeline with deterministic ownership at the seams, and turns a window that
could not be transcribed at all into a Korean marker naming the minutes that are
missing — so a loss is *visible inside* the transcript instead of *being* the
transcript.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, TypeAlias

import stt_gap

DEFAULT_WINDOW_MS: Final = 900_000
DEFAULT_OVERLAP_MS: Final = 15_000
MIN_WINDOW_MS: Final = 1_000
REPLACEMENT: Final = "\ufffd"

#: The line that stands in for a window nobody could transcribe. Its grammar lives in
#: `stt_gap` because the document layers have to recognize it again, not just print it.
GAP_MARKER: Final = stt_gap.MARKER

Segment: TypeAlias = Mapping[str, object]

_WHITESPACE: Final = re.compile(r"\s+")
_OFFSET_KEYS: Final = frozenset({"from", "to"})


@dataclass(frozen=True, slots=True)
class Window:
    """One slice of the recording, transcribed by its own whisper.cpp process."""

    index: int
    start_ms: int
    length_ms: int

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.length_ms


@dataclass(frozen=True, slots=True)
class WindowResult:
    """What one window produced, and where its offsets sit on the global timeline.

    ``offset_ms`` is what must be *added* to this window's segment offsets to place
    them on the recording's own timeline: 0 when whisper.cpp was given ``-ot`` (it
    then reports absolute offsets, verified on the node), ``window.start_ms`` when a
    window was cut out with ffmpeg and the decoder counted from zero.
    """

    window: Window
    segments: tuple[Segment, ...] = field(default_factory=tuple)
    offset_ms: int = 0


def plan_windows(
    duration_ms: int,
    *,
    window_ms: int = DEFAULT_WINDOW_MS,
    overlap_ms: int = DEFAULT_OVERLAP_MS,
) -> tuple[Window, ...]:
    """Windows of ``window_ms`` on a stride of ``window_ms - overlap_ms``, to the end.

    A recording no longer than one window is one window — short recordings keep the
    single-pass behaviour they always had. An unknown (0) duration plans nothing;
    the caller must not invent a length it could not read.
    """
    duration = max(int(duration_ms), 0)
    if duration <= 0:
        return ()
    window = max(int(window_ms), MIN_WINDOW_MS)
    if duration <= window:
        return (Window(index=0, start_ms=0, length_ms=duration),)
    overlap = min(max(int(overlap_ms), 0), window // 2)
    stride = window - overlap
    planned: list[Window] = []
    start = 0
    while True:
        end = min(start + window, duration)
        planned.append(Window(index=len(planned), start_ms=start, length_ms=end - start))
        if end >= duration:
            return tuple(planned)
        start += stride


def decode_payload(raw: bytes) -> tuple[str, int]:
    """Decode a whisper payload; returns the text and how many bytes had to be replaced.

    Strict UTF-8 first, because that is what a healthy payload is. When the decoder
    wrote a broken sequence — the 2026-09-04 failure — the bytes are decoded again
    with ``errors="replace"``: a mangled syllable is a mangled syllable, never a lost
    recording. The count is what was *introduced*, so a payload that legitimately
    contains U+FFFD is not reported as damaged.
    """
    try:
        return raw.decode("utf-8"), 0
    except UnicodeDecodeError:
        repaired = raw.decode("utf-8", "replace")
        introduced = repaired.count(REPLACEMENT) - raw.count(REPLACEMENT.encode("utf-8"))
        return repaired, max(introduced, 0)


def clock(ms: int) -> str:
    """Milliseconds as the HH:MM:SS the transcript's own headers print."""
    return stt_gap.clock(ms)


def gap_marker(window: Window, *, until: int | None = None) -> str:
    """The Korean line that takes the place of a window that could not be transcribed."""
    return stt_gap.marker(window.start_ms, window.end_ms if until is None else until)


def gap_result(window: Window, *, until: int | None = None) -> WindowResult:
    """A quarantined window as one visible segment covering the minutes it owns.

    ``until`` is where this window's own minutes end — the next window's start, which
    is earlier than ``window.end_ms`` by the overlap. The overlap belongs to the next
    window (see `merge`), and that window usually transcribed it: a marker claiming it
    too would send the owner back to minutes the document already has, and its span
    would reach past the segments that follow it on the timeline.

    The marker carries those offsets on purpose: the completeness check then reads the
    span as accounted for, which it is — it is named in the transcript. A silent hole
    is the failure that check exists to catch; a marked one is evidence.
    """
    end = window.end_ms if until is None else min(int(until), window.end_ms)
    segment: dict[str, object] = {
        "offsets": {"from": window.start_ms, "to": end},
        "text": stt_gap.marker(window.start_ms, end),
    }
    return WindowResult(window=window, segments=(segment,))


def shift(segment: Segment, delta: int) -> dict[str, object]:
    """A copy of ``segment`` with its own and its tokens' offsets moved by ``delta``.

    Only the millisecond offsets move; whisper's human-readable ``timestamps`` strings
    are left exactly as it wrote them, since the document is built from the offsets.
    """
    moved = dict(segment)
    if not delta:
        return moved
    offsets = segment.get("offsets")
    if isinstance(offsets, Mapping):
        moved["offsets"] = {
            key: (int(value) + delta if key in _OFFSET_KEYS and isinstance(value, (int, float))
                  else value)
            for key, value in offsets.items()
        }
    tokens = segment.get("tokens")
    if isinstance(tokens, list):
        moved["tokens"] = [
            shift(token, delta) if isinstance(token, Mapping) else token for token in tokens
        ]
    return moved


def start_ms(segment: Segment, *, default: int) -> int:
    """Where a segment starts, or ``default`` when the decoder reported no offsets."""
    offsets = segment.get("offsets")
    if isinstance(offsets, Mapping):
        value = offsets.get("from")
        if isinstance(value, (int, float)):
            return int(value)
    return default


def merge(results: Sequence[WindowResult]) -> tuple[dict[str, object], ...]:
    """One timeline out of many windows, with the seams decided by arithmetic.

    Windows overlap so no word falls into a cut, which means the same sentence can be
    decoded twice. Ownership settles it without any similarity guessing: a segment
    belongs to the window whose own stretch — from its start to the next window's
    start — contains the segment's global start. The last window keeps everything to
    the end of the recording, and a segment with no offsets belongs to the window that
    produced it.
    """
    ordered = sorted(results, key=lambda result: (result.window.start_ms, result.window.index))
    merged: list[dict[str, object]] = []
    for position, result in enumerate(ordered):
        lower = result.window.start_ms
        upper = ordered[position + 1].window.start_ms if position + 1 < len(ordered) else None
        for segment in result.segments:
            if not isinstance(segment, Mapping):
                continue
            moved = shift(segment, result.offset_ms)
            start = start_ms(moved, default=lower)
            if position and start < lower:
                continue
            if upper is not None and start >= upper:
                continue
            merged.append(moved)
    return tuple(merged)


def spans(segments: Sequence[Segment]) -> tuple[tuple[int, int], ...]:
    """The (start, end) pairs these segments account for — the completeness evidence."""
    found: list[tuple[int, int]] = []
    for segment in segments:
        offsets = segment.get("offsets") if isinstance(segment, Mapping) else None
        if not isinstance(offsets, Mapping):
            continue
        start, end = offsets.get("from"), offsets.get("to")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            found.append((int(start), int(end)))
    return tuple(found)


def text_of(segments: Sequence[Segment]) -> str:
    """The spoken text of these segments, joined the way the transcript reads them."""
    joined = " ".join(
        str(segment.get("text", "")).strip()
        for segment in segments
        if isinstance(segment, Mapping)
    )
    return _WHITESPACE.sub(" ", joined).strip()


def cache_key(*, audio_sha256: str, model: str, windows: Sequence[Window]) -> str:
    """Identity of one plan over one recording with one model — the resume key.

    Any of the three changing makes a different key, so a resumed run can never mix a
    window decoded from other audio, by another model, or under another cut.
    """
    plan = ";".join(f"{w.index}:{w.start_ms}:{w.length_ms}" for w in windows)
    material = f"{audio_sha256}|{model}|{plan}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
