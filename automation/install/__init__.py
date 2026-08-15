"""Third-party installation helpers (prerequisite checks and trust bootstrap).

This package holds the pieces a third-party operator needs *before* and *during*
a first install. It deliberately contains no account creation, no systemd
rendering and no privileged orchestration — that is the installer's own scope
(W-F1-B). Everything here is either read-only or a single, explicitly requested
root-owned file write, and every module separates pure decision logic from the
side effects so the installer can reuse the logic under `--dry-run`.
"""

from __future__ import annotations
