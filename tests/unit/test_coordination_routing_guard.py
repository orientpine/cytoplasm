"""Reciprocal routing guard on coordination (post-incident 2026-07-20).

coordination must refuse a request that fixes ONE exact slot (calendar-intent
whose title merely names a peer) BEFORE it queries the peer — the missing
guard that let a fixed 10:00 request drift to a negotiated 09:00 on another day.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "calendar" / "scripts"))
sys.path.insert(0, str(_REPO / "skills" / "coordination" / "scripts"))

coordinate_cli = import_module("coordinate_cli")
coordinate_io = import_module("coordinate_io")
coordination_time = import_module("coordination_time")

KST = timezone(timedelta(hours=9), "KST")


def _range(start_h: int, start_m: int, end_h: int, end_m: int):
    day = datetime(2026, 7, 22, tzinfo=KST)
    return coordination_time.RequestRange(
        start=day.replace(hour=start_h, minute=start_m),
        end=day.replace(hour=end_h, minute=end_m),
    )


def _args(*, summary: str = "peer-test 미팅", when: str | None = None, duration_min: int = 30):
    return argparse.Namespace(summary=summary, when=when, duration_min=duration_min)


# --- exact single slot → refuse to calendar -----------------------------------


def test_exact_slot_no_cue_is_refused_before_network() -> None:
    # 10:00–10:30, duration 30 → window == duration → exact single slot.
    with pytest.raises(coordinate_io.CoordinationError) as excinfo:
        coordinate_cli._reject_calendar_intent(_args(), _range(10, 0, 10, 30))
    assert excinfo.value.exit_code == 2
    assert "ROUTING-REJECT" in str(excinfo.value)
    assert "calendar" in str(excinfo.value)


def test_exact_slot_via_explicit_when_period_is_still_a_window() -> None:
    # A period window (오전 09:00–12:00) is wider than the duration → allowed.
    # Guard must NOT fire; coordination proceeds.
    coordinate_cli._reject_calendar_intent(_args(when="오전"), _range(9, 0, 12, 0))


# --- window request → allowed (guard is a no-op) ------------------------------


def test_window_request_passes_guard() -> None:
    # 09:00–18:00 with a 30-min duration is a genuine negotiation window.
    coordinate_cli._reject_calendar_intent(_args(), _range(9, 0, 18, 0))


# --- exact slot BUT explicit coordination cue → allowed (owner asked to negotiate)


def test_exact_slot_with_coordination_cue_is_allowed() -> None:
    # Even a duration-width window is allowed when the owner explicitly asked to
    # coordinate (cue in summary/when); the guard defers to that intent.
    coordinate_cli._reject_calendar_intent(
        _args(summary="peer-test 조율 미팅"), _range(10, 0, 10, 30)
    )


def test_exact_slot_cue_in_when_is_allowed() -> None:
    coordinate_cli._reject_calendar_intent(
        _args(summary="미팅", when="가능한 시간"), _range(10, 0, 10, 30)
    )
