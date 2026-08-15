"""Lifecycle failure bridge into the agent-local repair CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

from automation.repair.repair_redaction import redact


REPAIR_CLI: Final = Path(os.environ.get("REPAIR_CLI", "~/.hermes/repair/automation/repair/repair_cli.py")).expanduser()


def record_lifecycle_failure(event_type: str, context: dict[str, str]) -> None:
    """Hand a Hermes error to repair storage without echoing the raw failure."""
    raw_error = context.get("error")
    if not raw_error:
        return
    location = context.get("task_id", context.get("session_id", "hermes-lifecycle"))
    completed = subprocess.run(
        (
            "python3",
            "-I",
            str(REPAIR_CLI),
            "detect",
            "--source",
            f"agent:{event_type}",
            "--location",
            location,
            "--stdin",
        ),
        capture_output=True,
        check=False,
        input=raw_error,
        text=True,
        timeout=90,
    )
    if completed.returncode != 0:
        print(f"repair reporter failed rc={completed.returncode}: {redact(completed.stderr)[:200]}", file=sys.stderr)
