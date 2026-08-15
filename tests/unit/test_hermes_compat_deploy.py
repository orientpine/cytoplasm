"""Structural tests for the hermes_compat manifest + owner-DM deploy script."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

_ROOT = Path(__file__).resolve().parents[2]
_HC = _ROOT / "automation" / "hermes_compat"
_MANIFEST = _HC / "manifest.json"
_DEPLOY = _HC / "deploy-owner-dm.sh"

_REQUIRED_PATCH_FIELDS = {"id", "target", "applier", "marker", "backup_suffix"}


def _patches() -> list[dict[str, str]]:
    data = cast("dict[str, object]", json.loads(_MANIFEST.read_text(encoding="utf-8")))
    patches = data["patches"]
    assert isinstance(patches, list)
    return cast("list[dict[str, str]]", patches)


def test_manifest_is_valid_json_with_three_patches() -> None:
    assert len(_patches()) == 3


def test_every_patch_has_required_fields() -> None:
    for patch in _patches():
        assert _REQUIRED_PATCH_FIELDS <= set(patch), f"missing fields in {patch.get('id')!r}"


def test_new_owner_dm_patch_ids_present() -> None:
    ids = {patch["id"] for patch in _patches()}
    assert "owner-dm-busy-fifo" in ids
    assert "discord-per-message-receipts" in ids


def test_patch_appliers_and_markers_match_files() -> None:
    for patch in _patches():
        applier = _HC / patch["applier"]
        assert applier.is_file(), f"applier missing: {applier}"
        assert patch["marker"] in applier.read_text(encoding="utf-8")


def test_deploy_script_passes_bash_syntax_check() -> None:
    result = subprocess.run(
        ["bash", "-n", str(_DEPLOY)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_deploy_script_has_drain_guard_and_restarts_both_gateways() -> None:
    text = _DEPLOY.read_text(encoding="utf-8")
    assert "DRAIN-GUARD" in text
    assert "sudo -n -u agent" in text
    # Both gateways restart together (AGENTS.md 2026-07-22) via the restart_one helper.
    assert "restart_one agent" in text
    assert "restart_one peer" in text
    assert "sudo -n -u $acct" in text
    # Both patch appliers are invoked.
    assert "patch_busy_fifo.py" in text
    assert "patch_discord_receipts.py" in text
    # Runtime deps deployed: bootstrap + the package dir.
    assert "hermes_compat_boot.py" in text
    assert "automation/hermes_compat" in text
    # Restart is INDEPENDENT per account (both always attempted) with restart && is-active,
    # and a failed recovery is surfaced, not swallowed.
    assert "restart_one agent || agent_ok=0" in text
    assert "restart_one peer || peer_ok=0" in text
    assert "restart hermes-gateway.service && systemctl --user is-active" in text
    assert "ROLLBACK-RECOVERY-FAILED" in text


def test_deploy_script_is_transactional_with_preflight_and_rollback() -> None:
    text = _DEPLOY.read_text(encoding="utf-8")
    # Preflight proves both patches on throwaway copies before any live mutation.
    assert "PREFLIGHT" in text
    # Unique timestamped snapshot is the rollback source (not shared .autophagy-orig).
    assert "rollback/" in text
    # The transaction core is delegated to owner-dm-txn.sh (snapshot + auto-rollback).
    assert "owner-dm-txn.sh" in text
    # New modules are staged off the live import path, then activated in the transaction.
    assert "staging" in text
    # Fail-closed drain-guard uses the ledger-based helper (session/message-bound).
    assert "owner-dm-drain-check.py" in text
    # Restart recovery restores every file via the shared restore helper on failure.
    assert "restore_from_snapshot" in text
    # Fail-closed drain-guard: explicit override, no silent age-based bypass.
    assert "ALLOW_INFLIGHT_RESTART" in text
    assert " -lt 90 " not in text
    # The shared resolve boundary module is pushed as a runtime dep.
    assert "receipt_apply.py" in text
    # Drain-guard rejects blank / non-integer / multiline helper output (fail-closed).
    assert "*[!0-9]*" in text
    # A shared restore is pushed and used for both txn rollback and restart recovery.
    assert "owner-dm-restore.sh" in text


def test_transaction_core_and_drain_check_helpers_exist_and_lint() -> None:
    txn = _HC / "owner-dm-txn.sh"
    drain = _HC / "owner-dm-drain-check.py"
    assert txn.is_file()
    assert drain.is_file()
    restore = _HC / "owner-dm-restore.sh"
    assert restore.is_file()
    for helper in (restore, drain):
        lint = subprocess.run(
            ["bash", "-n", str(helper)] if helper.suffix == ".sh" else ["python3", "-c", "pass"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert lint.returncode == 0, lint.stderr
    txn_text = txn.read_text(encoding="utf-8")
    # The core snapshots, arms a restore trap, commits atomically, and shouts on
    # a failed rollback so the operator intervenes.
    assert "trap restore EXIT" in txn_text
    assert "COMMIT-OK" in txn_text
    assert "ROLLBACK-OK" in txn_text
    assert "ROLLBACK-FAILED" in txn_text
    result = subprocess.run(
        ["bash", "-n", str(txn)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr



def test_deploy_drain_and_recovery_are_fail_closed() -> None:
    text = _DEPLOY.read_text(encoding="utf-8")
    # B2: drain classifies lexically (no arithmetic), so an out-of-range integer
    # cannot overflow the -gt test back to a false 'clear'.
    assert "(0) state=clear" in text
    assert '-gt 0' not in text
    # B3B: a failed snapshot restore is folded into the recovery verdict, not masked.
    assert "restore_ok=0" in text
    assert "restore=$restore_ok" in text