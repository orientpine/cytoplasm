from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "calendar" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

calendar_confirm = import_module("calendar_confirm")
calendar_gate = import_module("calendar_gate")


def test_bot_token_prefers_environment_over_secrets_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    secrets = tmp_path / ".env.secrets"
    secrets.write_text("DISCORD_BOT_TOKEN=filetoken\n", encoding="utf-8")
    monkeypatch.setattr(calendar_confirm.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "envtoken")

    # When
    token = calendar_confirm.bot_token()

    # Then
    assert token == "envtoken"


def test_bot_token_reads_trimmed_value_from_secrets_file_when_environment_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    secrets = tmp_path / ".env.secrets"
    secrets.write_text(
        "# cron secrets\nOTHER_TOKEN=ignored\n DISCORD_BOT_TOKEN = ' filetoken ' \n",
        encoding="utf-8",
    )
    monkeypatch.setattr(calendar_confirm.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    # When
    token = calendar_confirm.bot_token()

    # Then
    assert token == "filetoken"


def test_bot_token_fails_closed_when_environment_and_secrets_file_are_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setattr(calendar_confirm.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    # When / Then
    with pytest.raises(calendar_gate.GateError):
        calendar_confirm.bot_token()
