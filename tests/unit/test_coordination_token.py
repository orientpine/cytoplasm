from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "coordination" / "scripts"))

import coordinate_io  # noqa: E402


def test_discord_bot_token_prefers_environment_over_secrets_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    (tmp_path / ".env.secrets").write_text("DISCORD_BOT_TOKEN=filetoken\n", encoding="utf-8")
    monkeypatch.setattr(coordinate_io.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "envtoken")

    # When
    token = coordinate_io.discord_bot_token()

    # Then
    assert token == "envtoken"


@pytest.mark.parametrize("secret_value", ["filetoken", "'filetoken'", '\"filetoken\"'])
def test_discord_bot_token_reads_secrets_file_when_environment_is_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, secret_value: str
) -> None:
    # Given
    (tmp_path / ".env.secrets").write_text(
        f"# ignored comment\nDISCORD_BOT_TOKEN={secret_value}\n", encoding="utf-8"
    )
    monkeypatch.setattr(coordinate_io.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    # When
    token = coordinate_io.discord_bot_token()

    # Then
    assert token == "filetoken"


def test_discord_bot_token_fails_closed_when_no_source_is_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    monkeypatch.setattr(coordinate_io.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    # When / Then
    with pytest.raises(coordinate_io.CoordinationError, match="DISCORD_BOT_TOKEN 누락") as excinfo:
        coordinate_io.discord_bot_token()

    assert excinfo.value.exit_code == 3
