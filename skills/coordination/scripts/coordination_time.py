"""Pure KST time-window resolution for coordination requests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

import coordinate_io as io

_PERIOD_WINDOWS = (
    ("오전", time(9), time(12)),
    ("오후", time(12), time(18)),
    ("저녁", time(18), time(21)),
    ("종일", time(9), time(18)),
)
_DEFAULT_WINDOW = (time(9), time(18))


@dataclass(frozen=True, slots=True)
class RequestRange:
    """A validated, timezone-aware availability range."""

    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class RequestRangeInput:
    """Mutually exclusive natural-language and explicit range inputs."""

    when: str | None
    range_start: str | None
    range_end: str | None


def current_kst() -> datetime:
    """Return the current KST time from calendar_core's shared constant."""
    import calendar_core

    return datetime.now(calendar_core.KST)


def resolve_request_range(input_range: RequestRangeInput, now: datetime) -> RequestRange:
    """Resolve exactly one allowed request-range input form."""
    when = input_range.when
    has_explicit = input_range.range_start is not None or input_range.range_end is not None
    if when is not None:
        if has_explicit:
            raise io.CoordinationError("--when 또는 --range-start/--range-end 중 하나만 지정하세요", 2)
        return resolve_when_range(when, now)
    if not has_explicit:
        raise io.CoordinationError("--when 또는 --range-start/--range-end 중 하나만 지정하세요", 2)
    return resolve_explicit_range(input_range.range_start, input_range.range_end)


def resolve_when_range(when: str, now: datetime) -> RequestRange:
    """Resolve a Korean day expression and period into a future KST range."""
    import calendar_core

    now_kst = now.astimezone(calendar_core.KST)
    day = calendar_core.resolve_relative_day(when, now_kst)
    start_time, end_time = _period_window(when)
    start = datetime.combine(day, start_time, calendar_core.KST)
    if start <= now_kst:
        raise io.CoordinationError(f"과거 시간 범위입니다: {start.isoformat()}", 2)
    return RequestRange(start=start, end=datetime.combine(day, end_time, calendar_core.KST))


def resolve_explicit_range(range_start: str | None, range_end: str | None) -> RequestRange:
    """Parse an explicit ISO range without changing its existing semantics."""
    if range_start is None or range_end is None:
        raise io.CoordinationError("--range-start와 --range-end를 함께 지정하세요", 2)
    return RequestRange(start=_aware(range_start, "--range-start"), end=_aware(range_end, "--range-end"))


def _period_window(when: str) -> tuple[time, time]:
    matches = tuple((start, end) for marker, start, end in _PERIOD_WINDOWS if marker in when)
    if len(matches) > 1:
        raise io.CoordinationError("시간대는 오전/오후/저녁/종일 중 하나만 지정하세요", 2)
    return matches[0] if matches else _DEFAULT_WINDOW


def _aware(raw: str, flag: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise io.CoordinationError(f"{flag} ISO 형식 오류: {raw}", 2) from None
    if value.tzinfo is None:
        raise io.CoordinationError(f"{flag}에 시간대 오프셋이 필요합니다 (예: +09:00)", 2)
    return value
