#!/usr/bin/env python3
"""Budget watcher (W4-3) — Hermes cron job, no_agent script mode, every 30 min.

Thin wrapper: runs the mounted budget skill CLI `watch` subcommand (approved
pending drafts are sent through the owner-approval gate, then the balance tab is
snapshotted/diffed). no_agent semantics: empty stdout + exit 0 on success
(silent tick); on failure prints one masked line and exits 1 so the scheduler
records an alert. Deployed copy lives at ~/.hermes/scripts/budget_watch.py
(Hermes cron sandbox rule); the skill CLI stays the single implementation at
~/.hermes/skills/budget/scripts/ — no import of it here, subprocess only
(avoids the W3-2 cron-sandbox PYTHONPATH package-shadowing trap).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

CLI = Path.home() / ".hermes" / "skills" / "budget" / "scripts" / "budget_cli.py"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")


def _redact(text: str) -> str:
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))


def main() -> int:
    if not CLI.exists():
        print("budget-watch error: budget skill is not mounted")
        return 1
    result = subprocess.run(  # noqa: S603 — fixed argv, agent-owned script
        [sys.executable, str(CLI), "watch"],
        capture_output=True, text=True, timeout=600, check=False,
        cwd=str(Path.home()),
    )
    if result.returncode == 0:
        return 0  # silent tick — stdout intentionally dropped
    tail = (result.stderr or result.stdout).strip().splitlines()
    detail = tail[-1] if tail else f"rc={result.returncode}"
    print(f"budget-watch error rc={result.returncode}: {_redact(detail)[:300]}")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — cron alert path: one masked line
        print(f"budget-watch error: {_redact(str(error))[:300]}")
        sys.exit(1)
