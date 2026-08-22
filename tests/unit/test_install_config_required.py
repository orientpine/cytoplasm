"""The installer must not guess which node it is installing.

`--config` is optional so that `--dry-run` works on a host that has nothing
configured yet. The cost of that convenience is that a *real* run without it
silently resolves to the tracked seed (`configs/node.example.toml`) and writes
another installation's accounts, paths and hostnames into root-owned systemd
and sudoers assets. `docs/guide/install.md` covers the hole with prose — every
documented invocation passes `--config` — but prose is not a guard.

The second half of this module pins the dry-run disclaimer: rc 0 from
`--dry-run` means "a plan was produced", never "the preconditions hold". The
checks are the only thing that can answer the latter and they do not run.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from automation.install.installer import main


def _key_line() -> str:
    algorithm = b"ssh-ed25519"
    blob = len(algorithm).to_bytes(4, "big") + algorithm
    blob += (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} config-required"


def _key(tmp_path: Path) -> Path:
    path = tmp_path / "update-trust.pub"
    _ = path.write_text(f"{_key_line()}\n", encoding="utf-8")
    return path


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "node.toml"
    _ = path.write_text(
        'origin_url = "ssh://git@example.invalid/node.git"\n',
        encoding="utf-8",
    )
    return path


def test_real_run_without_config_is_refused_before_a_plan_is_built(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a bundled trust key but no --config, and no --dry-run
    key = _key(tmp_path)

    # When
    code = main(("--update-trust-key", str(key)))

    # Then the run is refused by name, and nothing was planned from the seed
    captured = capsys.readouterr()
    assert code == 1
    assert "NODE-CONFIG-REQUIRED" in captured.err
    assert "--config" in captured.err
    assert "INSTALL PLAN" not in captured.out


def test_refusal_names_the_seed_that_would_otherwise_be_used(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    key = _key(tmp_path)

    # When
    _ = main(("--update-trust-key", str(key)))

    # Then the operator is told what the silent fallback was and how to opt out
    error = capsys.readouterr().err
    assert "node.example.toml" in error
    assert "/etc/autophagy/node.toml" in error
    assert "--dry-run" in error


def test_real_run_with_explicit_config_passes_the_config_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an explicit config, so only the root requirement can stop the run
    key = _key(tmp_path)
    config = _config(tmp_path)

    # When
    code = main(("--config", str(config), "--update-trust-key", str(key)))

    # Then the config gate let it through and the *root* gate is what refused
    captured = capsys.readouterr()
    assert code == 1
    assert "NODE-CONFIG-REQUIRED" not in captured.err
    assert "INSTALL PLAN" in captured.out
    assert "run the installer as root" in captured.out


def test_dry_run_without_config_stays_allowed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given no --config, which is the whole point of a first-look dry run
    key = _key(tmp_path)

    # When
    code = main(("--update-trust-key", str(key), "--dry-run"))

    # Then
    assert code == 0
    assert "INSTALL PLAN" in capsys.readouterr().out


def test_dry_run_states_that_checks_are_not_executed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    key = _key(tmp_path)

    # When
    code = main(("--update-trust-key", str(key), "--dry-run"))

    # Then rc 0 cannot be read as "preconditions satisfied"
    output = capsys.readouterr().out
    assert code == 0
    assert "checks are not executed in dry-run" in output
    assert "--dry-run에서는 실행되지 않는다" in output
