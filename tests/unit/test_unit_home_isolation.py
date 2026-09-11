"""Structural regression coverage for collection-time unit-test home isolation."""
from __future__ import annotations

import os
import pwd
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "calendar" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def test_path_home_uses_a_throwaway_directory() -> None:
    # Given: the account's passwd home independently of environment overrides.
    passwd_home = Path(pwd.getpwuid(os.getuid()).pw_dir)

    # When: the unit suite resolves its home directory.
    resolved_home = Path.home()

    # Then: it cannot be the developer's home and lives in the system temp root.
    assert resolved_home != passwd_home
    assert resolved_home.is_relative_to(Path(tempfile.gettempdir()))


def test_child_process_inherits_the_throwaway_home() -> None:
    # Given: a Python child process that resolves its own home directory.
    command = [sys.executable, "-c", "from pathlib import Path; print(Path.home())"]

    # When: the child inherits the unit-suite environment.
    result = subprocess.run(command, capture_output=True, text=True, check=True)

    # Then: it observes the exact HOME selected for this process.
    child_home = Path(result.stdout.strip())
    assert result.stdout.strip() == os.environ["HOME"]
    assert child_home != Path(pwd.getpwuid(os.getuid()).pw_dir)
    assert child_home.is_relative_to(Path(tempfile.gettempdir()))


def test_calendar_gate_default_resolves_outside_the_passwd_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: no calendar-directory override and the account's passwd home.
    monkeypatch.delenv("CALENDAR_GATE_DIR", raising=False)
    passwd_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    command = [
        sys.executable,
        "-c",
        (
            "import sys\n"
            f"sys.path.insert(0, {str(_SCRIPTS)!r})\n"
            "import calendar_gate\n"
            "print(calendar_gate._gate_dir())\n"
        ),
    ]

    # When: the production calendar-gate default is resolved in a child process.
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    gate_dir = Path(result.stdout.strip())

    # Then: it resolves under the throwaway temp root, not the passwd home.
    assert gate_dir.is_relative_to(Path(tempfile.gettempdir()))
    assert not gate_dir.is_relative_to(passwd_home)
