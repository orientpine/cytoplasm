from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import automation.interop.hermes_plugin as plugin
from automation.group_roster import MemberStatus, Roster, RosterAdmin, RosterMember
from automation.interop.delegation import InteropEnvelope, format_envelope

_REQUIRED_CONFIG: dict[str, str] = {
    "agent_id": "agent-cha",
    "agents_log_channel_id": "log-chan",
    "owner_id": "owner-1",
}
_PEER_BOT_ID = "100000000000000002"
_OTHER_BOT_ID = "100000000000000003"
_UNKNOWN_BOT_ID = "100000000000000099"
_ROSTER = Roster(
    schema=1,
    group_id="test-group",
    admin=RosterAdmin(
        name="Test Admin",
        discord_user_id="100000000000000001",
        publisher_principal="publisher-test-admin@autophagy",
        signing_public_key="unused-in-typed-fixture",
    ),
    members=[
        RosterMember("Peer", _PEER_BOT_ID, "peer-test", MemberStatus.ACTIVE),
        RosterMember("Other", _OTHER_BOT_ID, "peer-other", MemberStatus.ACTIVE),
    ],
)


def _write_config(path: Path, payload: dict[str, str]) -> Path:
    _ = path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_response_channel_when_coord_query_and_interop_channel_set_then_routes_to_interop_channel() -> None:
    # Given
    correlation_id = "coord-abc"

    # When
    channel = plugin.response_channel_for(correlation_id, source_channel_id="team", interop_channel_id="interop")

    # Then
    assert channel == "interop"


def test_response_channel_when_coord_query_without_interop_channel_then_falls_back_to_source() -> None:
    # Given
    correlation_id = "coord-abc"

    # When
    channel = plugin.response_channel_for(correlation_id, source_channel_id="team", interop_channel_id="")

    # Then
    assert channel == "team"


def test_response_channel_when_non_coord_query_then_stays_on_source_channel() -> None:
    # Given
    correlation_id = "w1-5-r1-abcd1234"

    # When
    channel = plugin.response_channel_for(correlation_id, source_channel_id="team", interop_channel_id="interop")

    # Then
    assert channel == "team"


def test_config_when_interop_channel_id_present_then_returned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    path = _write_config(tmp_path / "config.json", {**_REQUIRED_CONFIG, "interop_channel_id": "interop-chan"})
    monkeypatch.setenv("INTEROP_CONFIG", str(path))

    # When
    config = plugin._config()

    # Then
    assert config["interop_channel_id"] == "interop-chan"


def test_config_when_interop_channel_id_absent_then_valid_and_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    path = _write_config(tmp_path / "config.json", dict(_REQUIRED_CONFIG))
    monkeypatch.setenv("INTEROP_CONFIG", str(path))

    # When
    config = plugin._config()

    # Then
    assert config["interop_channel_id"] == ""

    # Given a config missing a REQUIRED field
    incomplete = {key: value for key, value in _REQUIRED_CONFIG.items() if key != "owner_id"}
    monkeypatch.setenv("INTEROP_CONFIG", str(_write_config(tmp_path / "incomplete.json", incomplete)))

    # Then
    with pytest.raises(ValueError, match="invalid private interop config"):
        plugin._config()


def _dispatch_query(
    correlation_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sender_id: str = "peer-test",
    author_id: str = _PEER_BOT_ID,
) -> tuple[dict[str, str], dict[str, str]]:
    # Given a hermetic HOME, an interop config with a dedicated channel, and a captured transport
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    config_path = _write_config(
        tmp_path / "config.json",
        {**_REQUIRED_CONFIG, "interop_channel_id": "interop-chan"},
    )
    monkeypatch.setenv("INTEROP_CONFIG", str(config_path))
    monkeypatch.setattr(plugin.group_roster, "load_roster", lambda _path: _ROSTER)
    captured: dict[str, str] = {}

    def _capture(*, channel_id: str, content: str) -> None:
        captured["channel_id"] = channel_id
        captured["content"] = content

    monkeypatch.setattr(plugin, "_send_to_channel", _capture)
    envelope = InteropEnvelope(
        correlation_id,
        sender_id,
        "agent-cha",
        "query_availability",
        {
            "range_start": "2026-07-21T09:00:00+09:00",
            "range_end": "2026-07-21T18:00:00+09:00",
            "duration_min": 30,
        },
    )
    event = SimpleNamespace(
        text=format_envelope(envelope),
        source=SimpleNamespace(
            chat_id="team-chan",
            is_bot=True,
            user_id=author_id,
            thread_id=None,
        ),
    )

    # When
    result = plugin.pre_gateway_dispatch(event, gateway=None, session_store=None)

    return captured, result


def test_pre_gateway_dispatch_when_coord_query_on_team_then_sends_response_to_interop_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When
    captured, result = _dispatch_query("coord-x", tmp_path, monkeypatch)

    # Then
    assert captured["channel_id"] == "interop-chan"
    assert result == {"action": "skip", "reason": "interop_delegation_response"}


def test_pre_gateway_dispatch_when_w15_query_then_response_goes_to_source_channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # When
    captured, result = _dispatch_query("w1-5-r1-abcd1234", tmp_path, monkeypatch)

    # Then
    assert captured["channel_id"] == "team-chan"
    assert result == {"action": "skip", "reason": "interop_delegation_response"}


def test_pre_gateway_dispatch_when_author_claims_another_roster_principal_then_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given / When: peer-other's Discord bot claims peer-test's envelope identity.
    with caplog.at_level(logging.WARNING, logger="autophagy.interop"):
        captured, result = _dispatch_query(
            "w1-5-forged",
            tmp_path,
            monkeypatch,
            sender_id="peer-test",
            author_id=_OTHER_BOT_ID,
        )

    # Then: the envelope is traced and rejected before any response is sent.
    assert captured == {}
    assert result == {"action": "skip", "reason": "interop_sender_identity_rejected"}
    assert "interop sender identity rejected" in caplog.text


def test_pre_gateway_dispatch_when_author_is_absent_from_roster_then_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given / When: an unknown Discord bot claims an otherwise valid sender_id.
    with caplog.at_level(logging.WARNING, logger="autophagy.interop"):
        captured, result = _dispatch_query(
            "w1-5-unknown",
            tmp_path,
            monkeypatch,
            author_id=_UNKNOWN_BOT_ID,
        )

    # Then: no delegation handling occurs and the rejection remains observable.
    assert captured == {}
    assert result == {"action": "skip", "reason": "interop_sender_identity_rejected"}
    assert "reason=sender_mismatch" in caplog.text
