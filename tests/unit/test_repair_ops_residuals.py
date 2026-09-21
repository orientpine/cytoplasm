"""Residual CLI regressions live here because the original ops test is frozen."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.repair import repair_ops_cli as cli
from automation.repair.repair_ops_approval import ManualOwnerApproval, repair_action_hash
from automation.repair.repair_ops_core import RepairOutcome, RepairPhase
from automation.repair.repair_ops_pending import PendingRepairApproval, PendingRepairApprovalStore
from automation.repair.repair_ops_reaction_watch import CliRepairApprovalCommands, RepairApprovalWatcher
from tests.unit.test_repair_approval_watch import FakeDiscord


@dataclass(frozen=True, slots=True)
class OutcomeAgent:
    outcome: RepairOutcome

    def repair(self, ticket_id: str, private_log: str) -> RepairOutcome:
        return self.outcome


def config_at(root: Path) -> cli.RepairOpsConfig:
    return cli.RepairOpsConfig(
        "t_abcdef", root / "deploy", root / "logs", root / "plans",
        root / "audit.jsonl", None, None, root / "work",
    )


def test_run_returns_waiting_exit_when_approval_is_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a valid run stopped at the owner approval gate.
    outcome = RepairOutcome(RepairPhase.AWAITING_APPROVAL, "t_abcdef", None, None)
    monkeypatch.setattr(cli, "_agent", lambda *args: OutcomeAgent(outcome))
    monkeypatch.setattr(cli, "private_log", lambda *args: "")
    # When: the CLI reports the result to its caller.
    code = cli._run(config_at(tmp_path), ManualOwnerApproval("owner", None))
    # Then: waiting is not success and remains machine-readable.
    assert code == 5
    assert json.loads(capsys.readouterr().out)["phase"] == "awaiting_approval"


def test_watcher_retains_record_when_child_exits_waiting(tmp_path: Path) -> None:
    # Given: a real child process returns the dedicated waiting result.
    script = tmp_path / "automation" / "repair" / "waiting.py"
    script.parent.mkdir(parents=True)
    script.write_text("raise SystemExit(5)\n", encoding="utf-8")
    now = datetime(2026, 9, 4, tzinfo=UTC)
    pending = PendingRepairApproval("t_abcdef", "patch.diff", repair_action_hash("t_abcdef", "patch.diff"), "nonce", "message", now)
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(pending)
    commands = CliRepairApprovalCommands(script, "synthetic")
    watcher = RepairApprovalWatcher(store, FakeDiscord(), commands, "owner", tmp_path / "audit", lambda: now)
    # When: the watcher dispatches an approval but the child does not apply it.
    watcher.dispatch(pending, "approved")
    # Then: neither retirement nor a false approved audit record occurs.
    assert store.get(pending.ticket_id) == pending
    assert not watcher.approval_log.exists()
