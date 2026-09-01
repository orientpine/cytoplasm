"""Rendering contract for the workstation release-completer units."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_INSTALLER: Final = _REPO / "automation" / "release_complete_install.sh"


def test_print_renders_both_units_without_touching_systemd() -> None:
    result = subprocess.run(
        ("bash", str(_INSTALLER), "--print"),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "# ==> autophagy-release-complete.service" in result.stdout
    assert "# ==> autophagy-release-complete.timer" in result.stdout
    assert str(_REPO.resolve()) in result.stdout
    assert (
        f"ExecStart=/usr/bin/bash {_REPO.resolve()}/automation/release_complete.sh"
        in result.stdout
    )
    assert "OnUnitActiveSec=2min" in result.stdout
    assert "$RELEASE_COMPLETE_REPO" not in result.stdout
