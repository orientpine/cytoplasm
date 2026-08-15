"""A plain install must run on a host that has nothing but CPython.

The installer is the *first* command a third party ever runs, before any package
manager step exists in the procedure. When `automation.install.installer` imported
the roster parser at module scope it dragged PyYAML in with it, so `--dry-run` died
with a bare `ModuleNotFoundError` traceback on a clean `python:3.12-slim` host —
destroying the property docs/qa/P0-5 measured and docs/guide/install.md promises:
every unmet precondition is *named*, never a traceback.

These tests pin the boundary in both directions: without PyYAML a plain install
still plans, and a *group* install (which genuinely needs the roster parser) fails
by name instead of by traceback.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

_GROUP_ROSTER_MODULES = (
    "automation.group_roster",
    "automation.group_roster.fetch",
    "automation.group_roster.parser",
    "automation.group_roster.schema",
    "automation.group_roster.validator",
)


def _ssh_string(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def _key_line() -> str:
    material = base64.b64encode(
        _ssh_string(b"ssh-ed25519") + _ssh_string(bytes(range(32)))
    ).decode("ascii")
    return f"ssh-ed25519 {material} update-trust@example"


def _without_pyyaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `import yaml` fail the way a clean host does.

    A None entry in sys.modules turns the import into ImportError, and the roster
    modules are dropped so an earlier test's cached copy cannot mask the regression.
    """
    monkeypatch.setitem(sys.modules, "yaml", None)
    for name in _GROUP_ROSTER_MODULES:
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_plain_dry_run_when_pyyaml_is_absent_then_still_prints_the_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    from automation.install.installer import main as installer_main

    key_path = tmp_path / "update-trust.pub"
    _ = key_path.write_text(f"{_key_line()}\n", encoding="utf-8")
    _without_pyyaml(monkeypatch)

    # When
    code = installer_main(("--update-trust-key", str(key_path), "--dry-run"))

    # Then
    assert code == 0
    assert "INSTALL PLAN" in capsys.readouterr().out


def test_group_install_when_pyyaml_is_absent_then_names_the_missing_dependency(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    from automation.install.installer import main as installer_main

    key_path = tmp_path / "update-trust.pub"
    roster_path = tmp_path / "roster.yaml"
    _ = key_path.write_text(f"{_key_line()}\n", encoding="utf-8")
    _ = roster_path.write_text("schema_version: 1\n", encoding="utf-8")
    _without_pyyaml(monkeypatch)

    # When
    code = installer_main(
        (
            "--update-trust-key",
            str(key_path),
            "--group-roster",
            str(roster_path),
            "--expect-group-skill-fingerprint",
            "SHA256:placeholder",
            "--dry-run",
        )
    )

    # Then
    error = capsys.readouterr().err
    assert code == 1
    assert "INSTALL-BLOCK: GROUP-ROSTER-DEPENDENCY-MISSING" in error
    assert "PyYAML" in error
