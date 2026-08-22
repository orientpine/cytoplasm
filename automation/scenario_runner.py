"""One deterministic execution contract for every independent skill scenario review."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
_SCENARIO_TIMEOUT_SECONDS: Final = 30


def _environment(home: str) -> Mapping[str, str]:
    interop_runtime = Path(
        os.environ.get("INTEROP_RUNTIME", "~/.hermes/interop_runtime")
    ).expanduser()
    return {
        "HOME": home,
        "PATH": "/usr/bin:/bin",
        "AUTOPHAGY_DEMO_SECRET": "DUMMY-scenario-review",
        "AUTOPHAGY_REPO_ROOT": str(REPO_ROOT),
        "INTEROP_RUNTIME": str(interop_runtime),
    }


def scenario_passes(skill_dir: Path, output_file: Path | None) -> bool:
    """Run one staged scenario or validate the independently captured stage-one output."""
    resolved_skill_dir = skill_dir.resolve()
    scenario = resolved_skill_dir / "scripts" / "scenario.sh"
    if not scenario.is_file() or scenario.is_symlink():
        return False
    if output_file is not None:
        try:
            return "SCENARIO-PASS" in output_file.read_text(encoding="utf-8")
        except OSError:
            return False
    with tempfile.TemporaryDirectory(prefix="skill-review-") as home:
        try:
            completed = subprocess.run(
                ["bash", str(scenario)],
                cwd=resolved_skill_dir,
                env=_environment(home),
                check=False,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=_SCENARIO_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
    return completed.returncode == 0 and "SCENARIO-PASS" in completed.stdout
