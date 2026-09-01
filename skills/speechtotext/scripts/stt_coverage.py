"""Did the transcript actually cover the whole recording?

A long recording fails in a specific, quiet way: the decoder stops early or a
window collapses, and the transcript still *looks* fine — it is just missing
the last hour. Timestamped segments make that mechanically checkable, so this
module merges the segment spans and compares them against the true media
duration.

Silence is NOT a defect: a meeting with pauses legitimately leaves gaps, so a
raw coverage ratio is never the verdict. What the verdict looks at is an
unexplained span — audio at the head or tail with no segment at all, or an
internal hole far longer than an ordinary pause. And with no known duration
there is no evidence either way, so completeness is never *claimed*.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

DEFAULT_TOLERANCE_MS: Final = 1_000
DEFAULT_EDGE_GAP_MS: Final = 60_000
DEFAULT_INTERNAL_GAP_MS: Final = 120_000
DEFAULT_REPEAT_LIMIT: Final = 0.08
REPEAT_WINDOW: Final = 8

Span = tuple[int, int]


@dataclass(frozen=True, slots=True)
class Coverage:
    """How much of the recording the transcript accounts for."""

    duration_ms: int
    covered_ms: int
    ratio: float
    leading_gap_ms: int
    trailing_gap_ms: int
    gaps: tuple[Span, ...]
    complete: bool

    def summary(self) -> str:
        if self.duration_ms <= 0:
            return "확인 불가(길이 미상)"
        state = "누락 없음" if self.complete else "누락 의심"
        return f"{self.ratio * 100:.1f}% · 미검출 구간 {len(self.gaps)}곳 · {state}"


def merge_spans(
    spans: Sequence[Span], *, tolerance_ms: int = DEFAULT_TOLERANCE_MS
) -> tuple[Span, ...]:
    """Join spans that touch, overlap, or sit within ``tolerance_ms`` of each other."""
    ordered = sorted((int(start), int(end)) for start, end in spans if int(end) >= int(start))
    merged: list[Span] = []
    for start, end in ordered:
        if merged and start - merged[-1][1] <= tolerance_ms:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            continue
        merged.append((start, end))
    return tuple(merged)


def assess(
    spans: Sequence[Span],
    duration_ms: int,
    *,
    tolerance_ms: int = DEFAULT_TOLERANCE_MS,
    max_edge_gap_ms: int = DEFAULT_EDGE_GAP_MS,
    max_internal_gap_ms: int = DEFAULT_INTERNAL_GAP_MS,
) -> Coverage:
    """Verdict on whether any span of audio went untranscribed."""
    merged = merge_spans(spans, tolerance_ms=tolerance_ms)
    covered = sum(end - start for start, end in merged)
    duration = max(int(duration_ms), 0)
    if not merged:
        return Coverage(
            duration_ms=duration,
            covered_ms=0,
            ratio=0.0,
            leading_gap_ms=duration,
            trailing_gap_ms=duration,
            gaps=(),
            complete=False,
        )
    leading = merged[0][0]
    trailing = max(duration - merged[-1][1], 0) if duration else 0
    holes = tuple(
        (previous[1], current[0])
        for previous, current in zip(merged, merged[1:], strict=False)
        if current[0] - previous[1] > max_internal_gap_ms
    )
    complete = (
        duration > 0
        and leading <= max_edge_gap_ms
        and trailing <= max_edge_gap_ms
        and not holes
    )
    return Coverage(
        duration_ms=duration,
        covered_ms=covered,
        ratio=(covered / duration) if duration else 0.0,
        leading_gap_ms=leading,
        trailing_gap_ms=trailing,
        gaps=holes,
        complete=complete,
    )


def dominant_repeat(text: str, *, window: int = REPEAT_WINDOW) -> tuple[float, str]:
    """Share of the transcript taken by its single most repeated phrase, and that phrase.

    Coverage cannot see this failure. A collapsed long-form decode keeps emitting
    timestamped segments, so the timeline stays fully covered while the words become
    one sentence repeated for minutes. Measured on a real 94-minute Korean recording:
    a healthy 5-minute stretch sat at 1.2%, the collapsed full run at 57%.
    """
    words = text.split()
    if len(words) <= window:
        return 0.0, ""
    counts = Counter(tuple(words[index : index + window]) for index in range(len(words) - window))
    gram, hits = counts.most_common(1)[0]
    return min(hits * window / len(words), 1.0), " ".join(gram)
