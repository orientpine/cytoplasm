#!/usr/bin/env python3
"""no_agent wrapper for Hermes cron `mail-daily-digest` (08:00 KST) that sends
the owner mail digest; silent on success.

Thin wrapper: runs the mounted mail skill CLI `digest` subcommand. no_agent
semantics: empty stdout + exit 0 on success (silent tick); on failure prints one
masked line and exits 1 so the scheduler records an alert. Deployed copy lives
at ~/.hermes/scripts/mail_digest_watch.py (Hermes cron sandbox rule); the skill
CLI stays the single implementation in the immutable governed live store at
/srv/autophagy-skills/live/mail/scripts/ — no import of it here, subprocess
only (avoids the W3-2 cron-sandbox PYTHONPATH package-shadowing trap).

The CLI and this wrapper speak one contract: every digest failure surfaces as
exactly one structured ``DIGEST-FAIL stage=... retry_safe=... code=...`` line.
This wrapper passes a child marker through verbatim, or synthesizes a
``stage=runner`` marker when the child produced none (crash, non-marker exit,
or unmounted skill). In-tick retry fires ONLY for ``retry_safe=true`` markers;
a ``retry_safe=false`` failure (a delivery that may already have sent some
Discord chunks, or a build item that may already have delegated a calendar
draft) is never auto-replayed, so the owner never receives a duplicate.

The marker is written to STDOUT: Hermes ``--no-agent`` delivers the script's
stdout verbatim to the cron ``--deliver`` owner-DM target, and drops stderr
(verified in the 2026-07-31 saved cron output). Success is an empty stdout
(silent tick); a failure is the one marker line + exit 1, which the cron
delivers to cha — rather than the app re-sending a DM through the failing path.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

CLI = Path("/srv/autophagy-skills/live/mail/scripts/triage_cli.py")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")
_MARKER = re.compile(r"^DIGEST-FAIL stage=\S+ retry_safe=(true|false) code=\S+")
_RETRY_DELAYS_S = (20, 60, 120)


def _redact(text: str) -> str:
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))


def _run_digest() -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, agent-owned script
        [sys.executable, str(CLI), "digest"],
        capture_output=True, text=True, timeout=1800, check=False,
        cwd=str(Path.home()),
    )


def _child_marker(output: str) -> str | None:
    """Return the child's single structured DIGEST-FAIL marker line, if any."""
    for line in output.splitlines():
        if _MARKER.match(line.strip()):
            return line.strip()
    return None


def _is_retry_safe(marker: str) -> bool:
    """True only when the child explicitly declared the failure safe to replay."""
    return "retry_safe=true" in marker


def main() -> int:
    if not CLI.exists():
        print("DIGEST-FAIL stage=runner retry_safe=false code=not_mounted detail=mail skill is not mounted")
        return 1
    result = _run_digest()
    for delay in _RETRY_DELAYS_S:
        marker = _child_marker(result.stderr or result.stdout)
        if result.returncode == 0 or marker is None or not _is_retry_safe(marker):
            break
        time.sleep(delay)
        result = _run_digest()
    if result.returncode == 0:
        return 0
    marker = _child_marker(result.stderr or result.stdout)
    if marker is not None:
        print(_redact(marker)[:300])
    else:
        tail = (result.stderr or result.stdout).strip().splitlines()
        detail = _redact(tail[-1]) if tail else ""
        print(
            f"DIGEST-FAIL stage=runner retry_safe=false code=child_exit "
            f"child_rc={result.returncode} detail={detail[:200]}"
        )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — cron alert path: one masked marker line
        print(
            f"DIGEST-FAIL stage=runner retry_safe=false code=wrapper_crash detail={_redact(str(error))[:200]}"
        )
        sys.exit(1)
