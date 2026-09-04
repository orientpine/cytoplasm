"""Per-kind aggregation of approval events.

Definitions, fixed here so every report means the same thing:

* ``count`` — events read for the kind, decided or not.
* ``per_day`` — ``count`` divided by the observed span in whole days, where the span is
  the first-to-last ``created_at`` rounded up to at least one day.
* ``p50``/``p95`` — nearest-rank percentiles (index ``ceil(p * n) - 1``) over the waits
  of DECIDED events only; ``None`` when the ledger recorded no decision time.
* ``rerequest_rate`` — the share of events whose ``request_key`` occurs more than once,
  i.e. how often the owner was asked again about the same thing.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from automation.approval_kpi.model import ApprovalEvent, KindStats

_SECONDS_PER_DAY = 86_400.0


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Nearest-rank percentile — no interpolation, so every number is an observed wait."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _span_days(events: Sequence[ApprovalEvent]) -> float:
    first = min(event.created_at for event in events)
    last = max(event.created_at for event in events)
    return max(1.0, math.ceil((last - first).total_seconds() / _SECONDS_PER_DAY))


def _rerequest_rate(events: Sequence[ApprovalEvent]) -> float:
    seen: dict[str, int] = {}
    for event in events:
        seen[event.request_key] = seen.get(event.request_key, 0) + 1
    repeated = sum(total for total in seen.values() if total > 1)
    return repeated / len(events)


def stats_for(kind: str, events: Sequence[ApprovalEvent]) -> KindStats:
    """Collapse one kind's events into the reported KPI row."""
    waits = [event.wait_seconds for event in events]
    decided = [wait for wait in waits if wait is not None]
    manual = sum(1 for event in events if event.manual_reaction)
    return KindStats(
        kind=kind,
        count=len(events),
        decided=len(decided),
        per_day=len(events) / _span_days(events),
        p50_seconds=percentile(decided, 0.5),
        p95_seconds=percentile(decided, 0.95),
        rerequest_rate=_rerequest_rate(events),
        manual_reaction_rate=manual / len(events),
    )


def aggregate(events: Iterable[ApprovalEvent]) -> tuple[KindStats, ...]:
    """Group events by kind and report each kind's burden, sorted by kind name."""
    grouped: dict[str, list[ApprovalEvent]] = {}
    for event in events:
        grouped.setdefault(event.kind, []).append(event)
    return tuple(stats_for(kind, grouped[kind]) for kind in sorted(grouped))
