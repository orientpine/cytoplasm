"""Deployed skills must import from their hash-named release path.

Regression: mounted skills live at
/srv/autophagy-skills/releases/<skill>/<sha256>/scripts/, so a bootstrap that
assumes `parents[3]` is the repo root cannot resolve the absolute
`from skills.<skill>... import ...` statements the CLIs and their submodules use
(ModuleNotFoundError: No module named 'skills') — which broke the post-mount
invoke smoke. report already solved this class with dual-mode imports; this
locks the same guarantee for procurement and doctype.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("skill", "cli"),
    [("procurement", "procure_cli.py"), ("doctype", "doctype_cli.py")],
)
def test_cli_imports_from_hash_named_release_layout(tmp_path: Path, skill: str, cli: str) -> None:
    release = tmp_path / "releases" / skill / "deadbeefhash"
    release.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "skills" / skill, release)

    result = subprocess.run(  # noqa: S603
        [sys.executable, str(release / "scripts" / cli), "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert "No module named 'skills'" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("skill", "cli"),
    [("procurement", "procure_cli.py"), ("doctype", "doctype_cli.py")],
)
def test_cli_still_imports_from_canonical_repo_layout(skill: str, cli: str) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "skills" / skill / "scripts" / cli), "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
