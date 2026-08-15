#!/usr/bin/env python3
"""Hermes no-agent weekly runner that records the existing full bank result."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

try:
    from automation.regression_bank.bank_state import DEFAULT_STATE_PATH, record_result
except ImportError:
    sys.path.insert(0, str(Path.home() / ".hermes" / "regression_bank_runtime"))
    from bank_state import DEFAULT_STATE_PATH, record_result


BANK_TIMEOUT_SECONDS: Final = 1800


def run_weekly_bank(repo_root: Path, state_path: Path) -> int:
    """Run the unchanged ``--all`` bank once and atomically persist its exit result."""
    completed = subprocess.run(
        ("bash", str(repo_root / "tests/e2e/run_bank.sh"), "--all"),
        capture_output=True,
        check=False,
        text=True,
        timeout=BANK_TIMEOUT_SECONDS,
        cwd=repo_root,
    )
    _ = record_result(state_path, completed.returncode)
    return completed.returncode


def main(argv: list[str]) -> int:
    """Run under Hermes with deployment-owned root and private state paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(os.environ.get("REGRESSION_BANK_ROOT", "/srv/autophagy-agents"))
    )
    parser.add_argument(
        "--state-file", type=Path, default=Path(os.environ.get("REGRESSION_BANK_STATE", str(DEFAULT_STATE_PATH)))
    )
    arguments = parser.parse_args(argv)
    return run_weekly_bank(arguments.repo_root, arguments.state_file)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
