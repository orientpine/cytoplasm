from __future__ import annotations

from pathlib import Path

import pytest

from skills.meeting import plugin


def test_meeting_child_environment_loads_credentials_for_detached_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    secrets_file = tmp_path / ".env.secrets"
    _ = secrets_file.write_text(
        "DISCORD_BOT_TOKEN=meeting-discord-token\n" + "LITELLM_AGENT_KEY=meeting-litellm-key\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("LITELLM_AGENT_KEY", raising=False)
    monkeypatch.setattr(plugin, "ENV_SECRETS", secrets_file, raising=False)

    # When
    environment = plugin.child_environment()

    # Then
    assert environment["DISCORD_BOT_TOKEN"] == "meeting-discord-token"
    assert environment["LITELLM_AGENT_KEY"] == "meeting-litellm-key"


def test_launch_raises_typed_error_for_empty_trigger() -> None:
    with pytest.raises(plugin.EmptyMeetingTriggerError):
        plugin._launch(plugin.Trigger(chat_id="C1", doc_paths=(), body=None), "python3")
    assert issubclass(plugin.EmptyMeetingTriggerError, ValueError)
