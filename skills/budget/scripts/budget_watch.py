#!/usr/bin/env python3
"""Budget watcher (W4-3) — Hermes cron job, no_agent script mode, every 30 min.

Thin wrapper: runs the governed live budget skill CLI `watch` subcommand (approved
pending drafts are sent through the owner-approval gate, then the balance tab is
snapshotted/diffed). no_agent semantics: empty stdout + exit 0 on success
(silent tick); on failure prints one masked line and exits 1 so the scheduler
records an alert. Deployed copy lives at ~/.hermes/scripts/budget_watch.py
(Hermes cron sandbox rule); the skill CLI stays the single implementation in the
governed live store — no import of it here, subprocess only
(avoids the W3-2 cron-sandbox PYTHONPATH package-shadowing trap).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

_LIVE_SCRIPTS: Final = "/srv/autophagy-skills/live/budget/scripts"
_SCRIPTS = Path(os.environ.get("BUDGET_SCRIPTS", _LIVE_SCRIPTS)).expanduser()
_ENV_SECRETS: Final = Path.home() / ".env.secrets"
CLI = _SCRIPTS / "budget_cli.py"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")


def _redact(text: str) -> str:
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))


def _load_env_secrets(path: Path = _ENV_SECRETS) -> None:
    """no-agent cron gets no secrets in os.environ, so the parent loads them itself.

    Measured 2026-08-18: with `BUDGET_SHEET_ID` present in ~/.env.secrets the tick
    still died on `GATE-REFUSED BUDGET_SHEET_ID가 없습니다 (fail-closed)`, while the
    same wrapper run after `set -a; . ~/.env.secrets` exited 0 — the configuration
    was fine and simply never reached the child. Same shape as
    todo_confirm_reaction_watch._load_env_secrets.

    budget was only the watcher whose missing configuration happened to be *visible*
    (a sheet id the gate names out loud). Since 2026-08-20 the same contract is checked
    across every watcher a deploy script pushes to ~/.hermes/scripts/, by
    tests/unit/test_watcher_secret_propagation.py — five more were failing it.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def main() -> int:
    _load_env_secrets()
    if not CLI.exists():
        print("budget-watch error: budget skill is not mounted")
        return 1
    result = subprocess.run(  # noqa: S603 — fixed argv, agent-owned script
        [sys.executable, str(CLI), "watch"],
        capture_output=True, text=True, timeout=600, check=False,
        cwd=str(Path.home()),
        # Rule (b-2): state the child's environment explicitly rather than letting
        # it fall back — the credentials only exist because we just self-loaded them.
        env={**os.environ},
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
