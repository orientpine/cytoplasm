#!/usr/bin/env python3
"""설치 위치와 무관하게 한 번만 실행하는 no-agent 수집 래퍼."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import cast


def _runtime_root() -> Path:
    override = os.environ.get("AUTOPHAGY_REPO_ROOT") or os.environ.get("AUTOPHAGY_RUNTIME_ROOT")
    if override:
        return Path(override).expanduser()
    current = Path("/srv/autophagy-agent-current")
    return current if current.exists() else Path("/srv/autophagy-agents")


def _load_env_secrets() -> None:
    path = Path.home() / ".env.secrets"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for raw in lines:
        line = raw.strip().removeprefix("export ")
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and key.strip():
            _ = os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--once", action="store_true")
    _ = parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    try:
        _load_env_secrets()
        root = _runtime_root()
        sys.path.insert(0, str(root))
        environment = dict(os.environ)
        environment["AUTOPHAGY_REPO_ROOT"] = str(root)
        environment["AUTOPHAGY_RUNTIME_ROOT"] = str(root)
        command = [sys.executable, "-m", "automation.stt_eval.cron.capture_entry", "--once"]
        if cast(bool, args.verbose):
            command.append("--verbose")
        result = subprocess.run(command, cwd=root, env=environment, check=False, timeout=3600)
        return result.returncode
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"STT-EVAL-CAPTURE-FAIL reason={type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
