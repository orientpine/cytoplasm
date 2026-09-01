"""Proposal CLI environment-secret loading tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from skills.proposal.scripts import proposal_cli, proposal_env, proposal_preflight


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_load_env_secrets_loads_prefixed_absent_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = tmp_path / ".env.secrets"
    secrets.write_text(
        'export PROPOSAL_ENV_SECRET="proposal value"\nKIMM_DOCBOT_ENV_SECRET=\'docbot value\'\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("PROPOSAL_ENV_SECRET", raising=False)
    monkeypatch.delenv("KIMM_DOCBOT_ENV_SECRET", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    loaded = proposal_env.load_env_secrets(secrets)

    assert loaded == ("PROPOSAL_ENV_SECRET", "KIMM_DOCBOT_ENV_SECRET")
    assert os.environ["PROPOSAL_ENV_SECRET"] == "proposal value"
    assert os.environ["KIMM_DOCBOT_ENV_SECRET"] == "docbot value"


def test_load_env_secrets_preserves_existing_environment_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = tmp_path / ".env.secrets"
    secrets.write_text("PROPOSAL_ENV_SECRET=file-value\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PROPOSAL_ENV_SECRET", "environment-value")

    loaded = proposal_env.load_env_secrets(secrets)

    assert loaded == ()
    assert os.environ["PROPOSAL_ENV_SECRET"] == "environment-value"


def test_load_env_secrets_ignores_non_prefixed_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = tmp_path / ".env.secrets"
    secrets.write_text("SOME_OTHER_SETTING=x\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SOME_OTHER_SETTING", raising=False)

    loaded = proposal_env.load_env_secrets(secrets)

    assert loaded == ()
    assert "SOME_OTHER_SETTING" not in os.environ


def test_load_env_secrets_missing_file_is_a_silent_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert proposal_env.load_env_secrets(tmp_path / ".env.secrets") == ()


def test_proposal_cli_preflight_loads_secrets_before_argument_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    chrome = _executable(tmp_path / "preview-chrome")
    (tmp_path / ".env.secrets").write_text(
        f"PROPOSAL_PREVIEW_CHROME={chrome}\n"
        "PROPOSAL_DOCBOT_PIN=0123456789abcdef0123456789abcdef01234567\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PROPOSAL_PREVIEW_CHROME", raising=False)
    monkeypatch.delenv("PROPOSAL_DOCBOT_PIN", raising=False)
    # A clean runner has none of REQUIRED_BINARIES; the seam under test is the env
    # file, so PATH lookups are stubbed exactly as test_proposal_preflight does.
    # The chrome check stays real: it is the configured file path, not a PATH hit.
    monkeypatch.setattr(proposal_preflight.shutil, "which", lambda _name: "/bin/tool")

    assert proposal_cli.main(["preflight", "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["checks"]["chrome"] == "present"
    assert report["stages"]["visual-review"] == "present"
