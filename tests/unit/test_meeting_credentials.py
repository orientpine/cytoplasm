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


def test_empty_trigger_looks_for_a_transcript_instead_of_refusing(monkeypatch) -> None:
    """`!meeting` 만 쓴 것은 오류가 아니라 "아직 회의록이 없는 전사본을 처리하라"는 요청이다."""
    spawned: list[list[str]] = []
    monkeypatch.setattr(plugin, "_spawn", spawned.append)

    plugin._launch(plugin.Trigger(chat_id="C1", doc_paths=(), body=None), "python3")
    plugin._launch(plugin.Trigger(chat_id="C1", doc_paths=(), body="   \n "), "python3")

    assert len(spawned) == 2, "본문이 공백뿐인 경우도 같은 요청이다"
    for argv in spawned:
        assert "--from-pending-transcript" in argv
        assert "--body-file" not in argv
