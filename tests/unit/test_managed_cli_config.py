from __future__ import annotations

from pathlib import Path

import pytest

from automation.managed_sync import cli
from automation.group_roster.parser import ROSTER_ENV
from tests.unit.managed_cli_fixtures import (
    PRINCIPAL,
    REQUIRED_KEYS,
    SEED_PATH,
    config_payload,
    config_skills,
    install_config,
    install_roster,
    load_json_mapping,
    roster_text,
)


def test_config_seed_when_parsed_then_has_all_required_keys_with_placeholders() -> None:
    payload = load_json_mapping(SEED_PATH)

    assert frozenset(payload) == REQUIRED_KEYS
    remote_url = payload["remote_url"]
    assert isinstance(remote_url, str) and remote_url.startswith("ssh://")
    assert "REPLACE_ME" in remote_url
    for key in ("mirror_dir", "quarantine_dir", "state_path"):
        value = payload[key]
        assert isinstance(value, str) and value.startswith("~/.hermes/managed-sync/")
    for options in config_skills(payload).values():
        assert isinstance(options, dict)
        assert frozenset(options) == frozenset({"opt_in", "pin"})
        assert isinstance(options["opt_in"], bool)
        assert options["pin"] is None


def test_load_config_when_roster_declares_principal_then_config_carries_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a subscriber whose group roster names a publisher other than this repo's owner.
    config_path = install_config(tmp_path, monkeypatch, config_payload(tmp_path))

    # When: the runtime config is resolved.
    config = cli.load_config(config_path)

    # Then: the trusted principal is exactly what the roster's admin declares.
    assert config.publisher_principal == PRINCIPAL


def test_sync_when_roster_is_absent_then_exit_2_names_the_roster_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a complete sync config but no roster at all (an unconfigured install).
    _ = install_config(tmp_path, monkeypatch, config_payload(tmp_path))
    missing = tmp_path / "absent-roster.yaml"
    monkeypatch.setenv(ROSTER_ENV, str(missing))

    # When/Then: the CLI refuses rather than trusting any principal.
    assert cli.main(["sync"]) == 2
    error_output = capsys.readouterr().err
    assert "CONFIG-ERROR" in error_output
    assert str(missing) in error_output


def test_sync_when_roster_is_invalid_then_exit_2_without_running_the_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a roster whose admin principal is outside the publisher namespace.
    _ = install_config(tmp_path, monkeypatch, config_payload(tmp_path))
    _ = install_roster(
        tmp_path,
        monkeypatch,
        roster_text(principal="attacker@example.invalid"),
    )

    def unreachable(*_args: str, **_kwargs: str) -> None:
        raise AssertionError("sync must not run without a valid publisher principal")

    monkeypatch.setattr(cli, "sync_all", unreachable)

    # When/Then: resolution fails closed before any fetch or verify.
    assert cli.main(["sync"]) == 2
    assert "CONFIG-ERROR" in capsys.readouterr().err


def test_sync_when_config_file_is_missing_then_exit_2_names_the_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "config.json"
    monkeypatch.setenv("MANAGED_SYNC_CONFIG", str(missing))

    assert cli.main(["sync"]) == 2

    error_output = capsys.readouterr().err
    assert "CONFIG-ERROR" in error_output
    assert str(missing) in error_output


def test_sync_when_required_key_is_missing_then_exit_2_names_the_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = config_payload(tmp_path)
    del payload["remote_url"]
    _ = install_config(tmp_path, monkeypatch, payload)

    assert cli.main(["sync"]) == 2
    assert "missing required key: remote_url" in capsys.readouterr().err


def test_sync_when_config_is_not_json_then_exit_2_names_the_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.json"
    _ = config_path.write_text("not-json{{{", encoding="utf-8")
    monkeypatch.setenv("MANAGED_SYNC_CONFIG", str(config_path))

    assert cli.main(["sync"]) == 2

    error_output = capsys.readouterr().err
    assert "not valid JSON" in error_output
    assert str(config_path) in error_output


def test_sync_when_skill_options_are_malformed_then_exit_2_names_the_skill_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = config_payload(tmp_path)
    config_skills(payload)["managed-demo"] = {"opt_in": True}
    _ = install_config(tmp_path, monkeypatch, payload)

    assert cli.main(["sync"]) == 2
    assert "skills.managed-demo" in capsys.readouterr().err
