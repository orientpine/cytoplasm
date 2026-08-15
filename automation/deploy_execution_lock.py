#!/usr/bin/env python3
"""Hold one crash-safe deployment execution lease until the orchestrator closes stdin."""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from automation.interop.approval_lease import FileKeyLease  # noqa: E402

GATE_DIR: Final = Path("~/.hermes/skill-gate").expanduser()
LOCK_HELD_EXIT: Final = 8
_SKILL: Final = re.compile(r"[a-z0-9][a-z0-9-]{1,40}")


def main(argv: Sequence[str] | None = None) -> int:
    """Acquire one skill execution key, signal readiness, and hold it through stdin EOF."""
    values = tuple(sys.argv[1:] if argv is None else argv)
    if len(values) != 2 or values[0] != "--skill" or _SKILL.fullmatch(values[1]) is None:
        print("EXECUTION-LOCK-INVALID", file=sys.stderr)
        return 2
    skill = values[1]
    key = f"skill-deploy-execution:{skill}"
    with FileKeyLease(GATE_DIR / "approval-leases").hold(key) as owned:
        if not owned:
            print(f"EXECUTION-LOCK-HELD skill={skill}", file=sys.stderr)
            return LOCK_HELD_EXIT
        print(f"EXECUTION-LOCK-ACQUIRED skill={skill}", flush=True)
        _ = sys.stdin.read()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
