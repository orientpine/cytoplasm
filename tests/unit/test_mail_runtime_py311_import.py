"""mail cron child must import on the Hermes no-agent Python 3.11 runtime.

The Hermes cron scheduler runs script-mode watchers on its own uv-managed
CPython 3.11, and ``mail_triage_watch`` spawns the mounted triage CLI with
``sys.executable`` — so the whole import chain must work without
``typing.override`` (3.12+). Measured 2026-08-31 on the primary node:
consecutive mail-triage-watch ticks died with ``ImportError`` at
``mail_runtime.py`` (rc=1) because the triage_gate refactor imported
``override`` straight from ``typing``. CI runs 3.12, so this probe deletes
``typing.override`` in a fresh interpreter before importing the exact chain
the cron child executes (``triage_cli`` → ``mail_preflight`` →
``mail_runtime``) — the same shim rule as ``automation/typing_compat.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "mail" / "scripts"

_PROBE = """
import typing
if hasattr(typing, "override"):
    del typing.override
import sys
sys.path.insert(0, sys.argv[1])
import triage_cli  # noqa: F401 — the module the cron child actually executes
import mail_runtime
error = mail_runtime.MailPreflightError("probe", 4)
assert "probe" in str(error)
print("OK-311")
"""


def test_mail_cron_chain_imports_without_typing_override() -> None:
    completed = subprocess.run(  # noqa: S603 — fixed argv, our own interpreter
        [sys.executable, "-c", _PROBE, str(_SCRIPTS)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "OK-311" in completed.stdout
