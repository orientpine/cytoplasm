#!/usr/bin/env python3
"""no_agent wrapper for the Hermes cron that archives MailOn attachments to Drive.

Thin wrapper, subprocess only: the sync CLI stays the single implementation in the
governed live store at /srv/autophagy-skills/live/mail/scripts/ while this copy is
deployed flat to ~/.hermes/scripts/ (Hermes cron sandbox rule) — importing the CLI
from here would re-enter the W3-2 cron-sandbox PYTHONPATH package-shadowing trap.

no_agent semantics: empty stdout + exit 0 on a successful tick, and exactly one
``MAIL-ATTACHMENT-DRIVE-FAIL code=<code>`` line + exit 1 on failure (Hermes
delivers stdout verbatim and drops stderr). The code comes from the child's JSON
failure object; a missing skill mount reports ``code=unmounted`` rather than a
silent no-op, because an unmounted archive looks exactly like an idle one.

no-agent cron hands the wrapper no secrets, so it loads ~/.env.secrets itself and
states the child environment explicitly (규약 b-2).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

SYNC: Final = Path("/srv/autophagy-skills/live/mail/scripts/mail_attachment_drive_sync.py")
SYNC_ENV: Final = "MAIL_ATTACHMENT_SYNC_CLI"
ENV_SECRETS: Final = Path.home() / ".env.secrets"
MARKER: Final = "MAIL-ATTACHMENT-DRIVE-FAIL"


def _sync_cli() -> Path:
    """The governed sync CLI — overridable only so tests can point at a stub."""
    override = os.environ.get(SYNC_ENV, "").strip()
    return Path(override).expanduser() if override else SYNC


def _load_env_secrets(path: Path = ENV_SECRETS) -> None:
    """Put ~/.env.secrets into this process's environment (cron provides none)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        key, separator, value = raw_line.strip().partition("=")
        if separator and key and not key.startswith("#") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _child_code(*streams: str) -> str:
    """The ``code`` field of the child's JSON failure object, or ``unknown``."""
    for stream in streams:
        for line in reversed((stream or "").strip().splitlines()):
            try:
                payload = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("code"):
                return str(payload["code"]).replace("\n", "_")[:64]
    return "unknown"


def main() -> int:
    _load_env_secrets()
    sync_cli = _sync_cli()
    if not sync_cli.exists():
        print(f"{MARKER} code=unmounted")
        return 1
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, agent-owned script
            [sys.executable, str(sync_cli)],
            capture_output=True, text=True, timeout=3600, check=False,
            cwd=str(Path.home()),
            env=dict(os.environ),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"{MARKER} code={type(error).__name__}")
        return 1
    if completed.returncode == 0:
        return 0
    print(f"{MARKER} code={_child_code(completed.stderr, completed.stdout)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
