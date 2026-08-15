"""Channel selection for coordination traffic (interop-channel migration).

Bot-to-bot §2 ``coord-`` envelopes (query_availability / query_confirm_slot)
must flow on the dedicated interop channel (#autophagy-agents), NOT on #team.
The terse human confirmation notice (``TEAM_NOTICE_PREFIX``) stays on #team.
"""

from __future__ import annotations

import argparse
import sys
from importlib import import_module
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "calendar" / "scripts"))
sys.path.insert(0, str(_REPO / "skills" / "coordination" / "scripts"))

coordinate_cli = import_module("coordinate_cli")
coordinate_io = import_module("coordinate_io")
coordination_lifecycle = import_module("coordination_lifecycle")
coordination = import_module("automation.interop.coordination")
delegation = import_module("automation.interop.delegation")


def test_interop_channel_id_when_env_override_then_returns_override(monkeypatch) -> None:
    # Given: the interop channel id is overridden via env (no network needed)
    monkeypatch.setenv("COORD_INTEROP_CHANNEL_ID", "chan-x")
    # When / Then: the override short-circuits, mirroring team_channel_id
    assert coordinate_io.interop_channel_id() == "chan-x"


def test_cmd_request_posts_availability_query_to_interop_channel_not_team(monkeypatch) -> None:
    # Given: stubbed runtime/config, interop channel resolved, team channel forbidden
    posted: list[tuple[str, str]] = []

    def _forbidden_team_channel() -> str:
        raise AssertionError("team channel must not be used for envelopes")

    def _capture_post(channel_id: str, content: str) -> str:
        posted.append((channel_id, content))
        return "msg-1"

    monkeypatch.setattr(coordinate_io, "ensure_runtime", lambda: None)
    monkeypatch.setattr(coordinate_io, "calendar_scripts", lambda: Path("."))
    monkeypatch.setattr(
        coordinate_io, "interop_config",
        lambda: {"agent_id": "agent-me", "owner_id": "owner-1"},
    )
    monkeypatch.setattr(
        coordinate_io, "interop_channel_id", lambda: "interop-chan", raising=False
    )
    monkeypatch.setattr(coordinate_io, "team_channel_id", _forbidden_team_channel)
    monkeypatch.setattr(coordinate_io, "post_message", _capture_post)
    monkeypatch.setattr(coordinate_io, "poll_envelope", lambda **kwargs: None)
    monkeypatch.setattr(
        coordinate_cli, "send_owner_dm", lambda *args, **kwargs: ("dm-chan", "dm-msg")
    )
    args = argparse.Namespace(
        peer="peer-agent", summary="peer-test 미팅", when=None,
        range_start="2099-07-22T09:00:00+09:00", range_end="2099-07-22T18:00:00+09:00",
        duration_min=30, calendar="primary", timeout_s=0.01,
        e2e_confirm=False, peer_decline=False,
    )

    # When: a window meeting request runs and the peer never responds
    exit_code = coordinate_cli.cmd_request(args)

    # Then: deadlock exit, and the one posted envelope went to the interop channel
    assert exit_code == coordinate_cli.EXIT_DEADLOCK
    assert len(posted) == 1
    channel, content = posted[0]
    assert channel == "interop-chan"
    envelope = delegation.parse_envelope(content)
    assert envelope is not None
    assert envelope.intent == coordination.QUERY_AVAILABILITY
    assert envelope.correlation_id.startswith(coordination.CORRELATION_PREFIX)


def test_lifecycle_team_notice_still_posts_to_team_channel(monkeypatch) -> None:
    # Given: the executed state machine emits post_team_confirmation
    posted: list[tuple[str, str]] = []

    def _capture_post(channel_id: str, content: str) -> str:
        posted.append((channel_id, content))
        return "msg-9999"

    monkeypatch.setattr(coordinate_io, "team_channel_id", lambda: "team-chan")
    monkeypatch.setattr(coordinate_io, "post_message", _capture_post)
    monkeypatch.setattr(
        coordination_lifecycle, "send_owner_dm",
        lambda *args, **kwargs: ("dm-chan", "dm-msg"),
    )
    slot = "2099-07-22T10:00:00+09:00"
    state, _ = coordination.on_owner_confirm(
        coordination.CoordinationState(
            phase=coordination.Phase.AWAIT_OWNER_CONFIRM, candidates=(slot,)
        ),
        True,
    )
    _, commands = coordination.on_executed(state)

    # When: the lifecycle finish path posts the human confirmation notice
    exit_code = coordination_lifecycle.finish(
        {"agent_id": "agent-me", "owner_id": "owner-1"}, "coord-test123", commands,
        "2099-07-22 (수) 10:00~10:30 KST", "peer-test 미팅", "event-abc123",
    )

    # Then: the notice went to #team and carries the cascade-safe prefix
    assert exit_code == 0
    assert len(posted) == 1
    channel, content = posted[0]
    assert channel == "team-chan"
    assert content.startswith(coordination.TEAM_NOTICE_PREFIX)
