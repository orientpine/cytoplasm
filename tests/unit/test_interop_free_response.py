from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.interop import free_response
from automation.interop.free_response import State, decide, run

_ID = "123456789012345678"
_NOW = datetime(2026, 10, 2, 4, 0, tzinfo=UTC)
_REST = "model:\n  provider: p\n# keep this comment\ntimezone: Asia/Seoul\n"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("discord:\n  free_response_channels: '1,2'\n", "discord:\n  free_response_channels: '1,2,{id}'\n"),
        ('discord:\n  free_response_channels: "1"\n', 'discord:\n  free_response_channels: "1,{id}"\n'),
        ("discord:\n  free_response_channels: 1,2\n", "discord:\n  free_response_channels: 1,2,{id}\n"),
        ("discord:\n  free_response_channels: [1, 2]\n", "discord:\n  free_response_channels: [1, 2, '{id}']\n"),
        ("discord:\n  free_response_channels:\n    - '1'\n  auto_thread: true\n",
         "discord:\n  free_response_channels:\n    - '1'\n    - '{id}'\n  auto_thread: true\n"),
        ("discord:\n  require_mention: true\n", "discord:\n  free_response_channels: '{id}'\n  require_mention: true\n"),
        ("timezone: x\n", "timezone: x\ndiscord:\n  free_response_channels: '{id}'\n"),
    ],
)
def test_a_missing_channel_is_added_once_in_the_existing_form(before: str, after: str) -> None:
    decision = decide(_REST + before, _ID)

    assert decision.state is State.MISSING
    assert decision.text == _REST + after.format(id=_ID)
    assert decide(decision.text, _ID).state is State.PRESENT


def test_a_present_channel_changes_nothing_even_when_listed_twice() -> None:
    text = f"discord:\n  free_response_channels: {_ID},9,{_ID}\n  reactions: true\n"

    assert decide(text, _ID) == free_response.Decision(State.PRESENT, _ID)


def test_an_ignored_channel_is_never_added() -> None:
    text = f"discord:\n  free_response_channels: '9'\n  ignored_channels: '{_ID}'\n"

    decision = decide(text, _ID)

    assert decision.state is State.IGNORED and decision.text is None


def test_no_channel_and_inline_mapping_leave_the_file_alone() -> None:
    assert decide("discord:\n  require_mention: true\n", "").state is State.NO_CHANNEL
    assert decide("discord: {require_mention: true}\n", _ID).state is State.UNSUPPORTED


def test_other_top_level_blocks_with_the_same_key_are_not_touched() -> None:
    text = f"slack:\n  free_response_channels: '{_ID}'\ndiscord:\n  reactions: true\n"

    decision = decide(text, _ID)

    assert decision.state is State.MISSING
    assert decision.text == f"slack:\n  free_response_channels: '{_ID}'\ndiscord:\n  free_response_channels: '{_ID}'\n  reactions: true\n"


def _home(tmp_path: Path, config: str, interop: dict[str, object] | None) -> tuple[Path, Path]:
    config_path = tmp_path / "config.yaml"
    _ = config_path.write_text(config, encoding="utf-8")
    config_path.chmod(0o600)
    interop_path = tmp_path / "interop.json"
    if interop is not None:
        _ = interop_path.write_text(json.dumps(interop), encoding="utf-8")
    return config_path, interop_path


def test_apply_backs_up_writes_once_and_is_idempotent(tmp_path: Path) -> None:
    original = _REST + "discord:\n  free_response_channels: '1'\n  ignored_channels: '2'\n"
    config, interop = _home(tmp_path, original, {"agent_chat_channel_id": _ID})

    code, line = run(config=config, interop=interop, apply=True, now=_NOW)
    again = run(config=config, interop=interop, apply=True, now=_NOW)

    assert code == 0 and line.startswith("FREE-RESPONSE-ADDED")
    assert again[0] == 0 and again[1].startswith("FREE-RESPONSE-PRESENT:")
    assert config.read_text(encoding="utf-8") == original.replace("'1'", f"'1,{_ID}'")
    assert (tmp_path / "config.yaml.bak-free-response-20261002T040000Z").read_text(encoding="utf-8") == original
    assert config.stat().st_mode & 0o777 == 0o600
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "config.yaml", "config.yaml.bak-free-response-20261002T040000Z", "interop.json"]


def test_without_apply_or_channel_nothing_is_written(tmp_path: Path) -> None:
    original = "discord:\n  require_mention: true\n"
    config, interop = _home(tmp_path, original, {"agent_chat_channel_id": _ID})
    peer_interop = tmp_path / "peer-interop.json"
    _ = peer_interop.write_text(json.dumps({"owner_id": "1"}), encoding="utf-8")

    assert run(config=config, interop=interop, apply=False)[1].startswith("FREE-RESPONSE-MISSING")
    assert run(config=config, interop=peer_interop, apply=True)[1].startswith("FREE-RESPONSE-SKIP")
    assert run(config=config, interop=tmp_path / "absent.json", apply=True)[1].startswith("FREE-RESPONSE-SKIP")
    assert config.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("config.yaml.bak-*"))
