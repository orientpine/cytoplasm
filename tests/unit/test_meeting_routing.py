"""Deterministic calendar↔coordination arbitration (post-incident 2026-07-20).

The classifier decides which single skill owns one meeting request so that a
peer-named request with an exact time can never dual-fire (solo calendar draft
AND a peer negotiation that drifts to a different slot).
"""

from __future__ import annotations

import sys
from datetime import datetime
from importlib import import_module
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "calendar" / "scripts"))

calendar_core = import_module("calendar_core")
calendar_routing = import_module("calendar_routing")

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=calendar_core.KST)  # Wednesday


@pytest.fixture(autouse=True)
def _peers_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = tmp_path / "peers.yaml"
    payload = "\n".join(
        (
            "version: 1",
            "peers:",
            "  agent-cha:",
            '    bot_user_id: "111111111111111111"',
            "    bot_name: Owner-Agent",
            "  peer-test:",
            '    bot_user_id: "222222222222222222"',
            "    bot_name: Test-Peer",
            "",
        )
    )
    _ = registry.write_text(payload, encoding="utf-8")
    monkeypatch.setenv("CALENDAR_PEERS_CONFIG", str(registry))


# --- exact-time signal --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["내일 오후 3시 실험 미팅", "모레 14:30 치과", "다음주 화요일 오전 10시 반 세미나"],
)
def test_resolves_to_exact_time_true_for_concrete_slots(text: str) -> None:
    assert calendar_routing.resolves_to_exact_time(text, NOW) is True


@pytest.mark.parametrize(
    "text",
    ["다음주쯤 미팅", "내일 미팅", "다음주 오전에 미팅", "peer-test랑 다음주 수요일 오전에 30분 미팅"],
)
def test_resolves_to_exact_time_false_for_windows_and_vague(text: str) -> None:
    assert calendar_routing.resolves_to_exact_time(text, NOW) is False


# --- no peer involved ---------------------------------------------------------


def test_no_peer_exact_time_is_calendar() -> None:
    assert calendar_routing.classify_meeting_request("내일 오후 3시 실험 미팅", NOW) == "calendar"


def test_no_peer_vague_is_clarify() -> None:
    assert calendar_routing.classify_meeting_request("내일 미팅 잡아줘", NOW) == "clarify"


# --- the incident: peer named + exact time → calendar (NOT coordination) -------


def test_peer_named_exact_time_no_cue_is_calendar() -> None:
    # "peer-test랑 ... 오전 10시" — owner fixed their own slot; peer name is a
    # title token. Must NOT trigger a negotiation that drifts to another day.
    result = calendar_routing.classify_meeting_request(
        "peer-test랑 다음주 수요일 오전 10시 30분 미팅", NOW
    )
    assert result == "calendar"


def test_peer_named_exact_time_via_summary_only_is_calendar() -> None:
    # Peer name only in the LLM-produced title, exact time in the text.
    result = calendar_routing.classify_meeting_request(
        "다음주 수요일 오전 10시 peer-test 미팅", NOW
    )
    assert result == "calendar"


# --- peer named + coordination intent → coordination --------------------------


def test_peer_named_window_with_cue_is_coordination() -> None:
    result = calendar_routing.classify_meeting_request(
        "peer-test와 다음주 오전에 가능한 시간 조율해줘", NOW
    )
    assert result == "coordination"


def test_explicit_peer_flag_window_is_coordination() -> None:
    result = calendar_routing.classify_meeting_request(
        "다음주 오전에 미팅", NOW, peer_flag="peer-test"
    )
    assert result == "coordination"


# --- conflicting / insufficient signals → clarify (fail-closed) ---------------


def test_peer_named_exact_time_with_cue_is_clarify() -> None:
    # "10시에 가능한지 물어봐" — fixed slot AND negotiate cue conflict; the
    # negotiator cannot honour an exact slot, so fail-closed to clarify.
    result = calendar_routing.classify_meeting_request(
        "peer-test에게 다음주 수요일 오전 10시 가능한지 물어봐", NOW
    )
    assert result == "clarify"


def test_bare_peer_name_without_cue_or_flag_is_clarify() -> None:
    # A bare peer name in vague free text is not enough to start a negotiation.
    result = calendar_routing.classify_meeting_request("peer-test 다음주 미팅", NOW)
    assert result == "clarify"


def test_explicit_exact_slot_flag_treats_peer_name_as_title() -> None:
    # An already-resolved single ISO slot passed by the caller is exact; with no
    # negotiate cue the peer name is a title token, so this is a solo calendar
    # event (never a negotiation that could drift off the fixed slot).
    result = calendar_routing.classify_meeting_request(
        "peer-test 미팅", NOW, peer_flag="peer-test", explicit_exact_slot=True
    )
    assert result == "calendar"
