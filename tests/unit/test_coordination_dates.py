"""Deterministic KST date-window resolution for coordination requests."""
from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "coordination" / "scripts"))
sys.path.insert(0, str(_REPO / "skills" / "calendar" / "scripts"))

import calendar_core  # noqa: E402
import coordinate_io  # noqa: E402
import coordination_time  # noqa: E402

NOW = datetime(2026, 7, 17, 10, 0, tzinfo=calendar_core.KST)


def test_resolve_relative_day_tomorrow_crosses_year_boundary() -> None:
    # Given
    new_years_eve = datetime(2026, 12, 31, 10, 0, tzinfo=calendar_core.KST)

    # When
    resolved = calendar_core.resolve_relative_day("내일", new_years_eve)

    # Then
    assert resolved == date(2027, 1, 1)


def test_resolve_when_range_uses_tomorrow_afternoon_in_kst() -> None:
    # Given / When
    resolved = coordination_time.resolve_when_range("내일 오후", NOW)

    # Then
    assert resolved.start == datetime(2026, 7, 18, 12, 0, tzinfo=calendar_core.KST)
    assert resolved.end == datetime(2026, 7, 18, 18, 0, tzinfo=calendar_core.KST)


@pytest.mark.parametrize(
    ("when", "expected_start", "expected_end"),
    [
        ("내일 오전", time(9, 0), time(12, 0)),
        ("내일 저녁", time(18, 0), time(21, 0)),
    ],
)
def test_resolve_when_range_maps_period_to_window(
    when: str, expected_start: time, expected_end: time
) -> None:
    # Given / When
    resolved = coordination_time.resolve_when_range(when, NOW)

    # Then
    assert resolved.start.timetz() == expected_start.replace(tzinfo=calendar_core.KST)
    assert resolved.end.timetz() == expected_end.replace(tzinfo=calendar_core.KST)


def test_resolve_when_range_rejects_past_window() -> None:
    # Given / When / Then
    with pytest.raises(coordinate_io.CoordinationError) as excinfo:
        coordination_time.resolve_when_range("오늘 오전", NOW)

    assert excinfo.value.exit_code == 2


def test_explicit_range_remains_available() -> None:
    # Given / When
    resolved = coordination_time.resolve_explicit_range(
        "2026-07-18T09:00:00+09:00", "2026-07-18T18:00:00+09:00"
    )

    # Then
    assert resolved.start == datetime(2026, 7, 18, 9, 0, tzinfo=calendar_core.KST)
    assert resolved.end == datetime(2026, 7, 18, 18, 0, tzinfo=calendar_core.KST)


@pytest.mark.parametrize(
    ("when", "range_start", "range_end"),
    [
        (None, None, None),
        ("내일 오후", "2026-07-18T12:00:00+09:00", "2026-07-18T18:00:00+09:00"),
    ],
)
def test_resolve_request_range_requires_exactly_one_input_method(
    when: str | None, range_start: str | None, range_end: str | None
) -> None:
    # Given
    input_range = coordination_time.RequestRangeInput(
        when=when, range_start=range_start, range_end=range_end
    )

    # When / Then
    with pytest.raises(coordinate_io.CoordinationError) as excinfo:
        coordination_time.resolve_request_range(input_range, NOW)

    assert excinfo.value.exit_code == 2
