"""Tests for the deploy drain-guard helper (fail-closed on unresolved receipts)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation.hermes_compat.receipt_ledger import ReceiptLedger

_ROOT = Path(__file__).resolve().parents[2]
_HC = _ROOT / "automation" / "hermes_compat"
_CHECK = _HC / "owner-dm-drain-check.py"


def _compat_layout(tmp_path: Path) -> Path:
    compat = tmp_path / "compat"
    pkg = compat / "automation" / "hermes_compat"
    pkg.mkdir(parents=True)
    shutil.copy(_HC / "hermes_compat_boot.py", compat / "hermes_compat_boot.py")
    for module in ("__init__.py", "receipt_ledger.py", "receipt_tracker.py"):
        shutil.copy(_HC / module, pkg / module)
    return compat


def _ledger(home: Path) -> ReceiptLedger:
    return ReceiptLedger(home / ".hermes" / "owner-dm-receipts" / "receipts.sqlite3")


def _run(compat: Path, home: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(compat))
    return subprocess.run(
        [sys.executable, str(_CHECK), str(compat)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_reports_unresolved_received_receipts(tmp_path: Path) -> None:
    # Given
    compat = _compat_layout(tmp_path)
    home = tmp_path / "home"
    ledger = _ledger(home)
    ledger.record_received("c", "m1")  # still received (in flight)
    ledger.record_received("c", "m2")
    ledger.resolve("m2", ok=True)  # finalized

    # When
    result = _run(compat, home)

    # Then
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_reports_zero_when_all_receipts_resolved(tmp_path: Path) -> None:
    # Given
    compat = _compat_layout(tmp_path)
    home = tmp_path / "home"
    ledger = _ledger(home)
    ledger.record_received("c", "m1")
    ledger.resolve("m1", ok=False)

    # When
    result = _run(compat, home)

    # Then
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"


def test_fails_closed_when_modules_unavailable(tmp_path: Path) -> None:
    # Given: a compat dir with no runtime modules -> cannot determine state.
    empty = tmp_path / "empty"
    empty.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    # When
    result = _run(empty, home)

    # Then
    assert result.returncode == 2
    assert "UNDETERMINED" in result.stderr
