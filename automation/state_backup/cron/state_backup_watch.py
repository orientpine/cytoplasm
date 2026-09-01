"""SC-3 no-agent cron wrapper: run the weekly encrypted state backup from the release.

Same contract as ``selfskill_audit_watch``: self-load ``~/.env.secrets`` (no-agent cron
carries no secrets), resolve the runtime root (release first, mirror fallback), and run
the module as a child with the credentials explicitly in its environment (규약 b-2).
The daily schedule plus the delivered-week watermark gives weekday catch-up after a
failed Monday tick without ever backing up twice in one week.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

ENV_SECRETS: Final = Path.home() / ".env.secrets"
RELEASE_CURRENT: Final = Path("/srv/autophagy-agent-current")
RESIDENT_MIRROR: Final = Path("/srv/autophagy-agents")


def _runtime_root() -> Path:
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return RELEASE_CURRENT if RELEASE_CURRENT.exists() else RESIDENT_MIRROR


def _load_env_secrets(path: Path = ENV_SECRETS) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        key, separator, value = raw_line.strip().partition("=")
        if separator and key and not key.startswith("#") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def run_once(repo_root: Path) -> int:
    environment = dict(os.environ)
    environment["AUTOPHAGY_REPO_ROOT"] = str(repo_root)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "automation.state_backup.backup", "--once"],
            cwd=repo_root,
            env=environment,
            check=False,
            timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"state-backup-watch child failed: {type(error).__name__}", file=sys.stderr)
        return 1
    return completed.returncode


def main(argv: tuple[str, ...] | None = None) -> int:
    arguments = tuple(sys.argv[1:]) if argv is None else argv
    if arguments not in ((), ("--once",)):
        print("usage: state_backup_watch.py [--once]", file=sys.stderr)
        return 2
    _load_env_secrets()
    return run_once(_runtime_root())


if __name__ == "__main__":
    raise SystemExit(main())
