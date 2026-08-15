#!/usr/bin/env python3
"""Report unresolved owner-DM receipts so the deploy drain-guard can fail closed.

A receipt in the content-free ledger is ``received`` from the moment a physical
owner DM is acknowledged (👀) until its turn finalizes (✅/❌). A positive count
therefore means a DM turn is (or was, before a crash) mid-flight — a restart
would interrupt it. Prints the integer count on stdout and exits 0 when it can
determine the state; exits 2 (UNDETERMINED) if the ledger/modules cannot be
read, so the caller treats "unknown" as "do not restart" (fail-closed).

Usage: owner-dm-drain-check.py [COMPAT_DIR]
  COMPAT_DIR defaults to ~/.hermes/hermes-compat; the runtime modules + bootstrap
  are imported from there so the check runs before any new build is activated.
"""

from __future__ import annotations

import importlib
import os
import sys


def _received_count(compat_dir: str) -> int | None:
    if compat_dir not in sys.path:
        sys.path.insert(0, compat_dir)
    try:
        _ = importlib.import_module("hermes_compat_boot")  # merges automation.__path__
        from automation.hermes_compat.receipt_ledger import (
            ReceiptLedger,
            default_ledger_path,
        )
    except Exception:
        return None
    try:
        states = ReceiptLedger(default_ledger_path()).states()
    except Exception:
        return None
    return sum(1 for state in states.values() if state == "received")


def main(argv: list[str]) -> int:
    compat_dir = argv[1] if len(argv) > 1 else os.path.expanduser("~/.hermes/hermes-compat")
    count = _received_count(compat_dir)
    if count is None:
        print("UNDETERMINED", file=sys.stderr)
        return 2
    print(count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
