"""Offline CLI scenarios: real lifecycle, sandbox, git apply, local push and gh wire."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from automation.regression_bank.bank_state import record_result
from automation.repair.repair_ops_cli import RepairOpsConfig
from automation.repair.repair_ops_pending import PendingRepairApproval
from tests.unit import test_repair_approval_preguard as approval_fixtures
from tests.unit import test_repair_pr_residuals as pr_fixtures
from tests.unit.test_repair_patch_apply_residuals import PATCH, git

approved = approval_fixtures.approved
github = pr_fixtures.github
repository = pr_fixtures.repository

BOOTSTRAP = """
import sys
from automation.repair import repair_ops_cli as cli
from automation.repair.repair_ops_adapters import StaticPlanner
from automation.repair.repair_ops_pending import PendingRepairApprovalStore
from tests.unit.test_repair_approval_preguard import ApprovedTransport
config = cli._config([sys.argv[1]])
pending = PendingRepairApprovalStore(cli._pending_root()).get(config.ticket_id)
assert pending is not None
cli.configured_discord = lambda: ApprovedTransport(pending)
cli.planner_for = lambda config: StaticPlanner(config.plans)
cli._push_repair_branch = lambda config, outcome: cli.RepairWorkClone(config.checkout, config.work_clone).push_branch(config.ticket_id)
sys.argv = ['repair_ops_cli.py', '--apply-approved', config.ticket_id]
raise SystemExit(cli.main())
"""


@pytest.mark.parametrize("changed", [False, True], ids=["approved", "content-mismatch"])
def test_cli_applies_only_approved_patch_when_owner_reacts(
    approved: tuple[RepairOpsConfig, PendingRepairApproval], github: pr_fixtures.GhSandbox,
    changed: bool,
) -> None:
    # Given: the full lifecycle uses isolated disk state and real local git;
    # only Discord/diagnosis and the GitHub wire are replaced, never apply.
    config, pending = approved
    root = github.clone.work_clone
    for relative in ("tests/e2e/drivers/w4_local.sh", "tests/e2e/run_bank.sh"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('test "$(cat automation/example.txt)" = approved\n', encoding="utf-8")
    (root / "docs/patch").mkdir(parents=True)
    (root / "docs/patch/.keep").touch()
    git(root, "add", ".")
    git(root, "commit", "-m", "test: offline bank")
    remote = root.parent / "remote.git"
    git(root, "init", "--bare", str(remote))
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "origin", "main")
    assert pending.patch_source_path is not None
    patch = Path(pending.patch_source_path)
    (patch.parent / "repro.sh").write_text('test "$(cat automation/example.txt)" = approved\n', encoding="utf-8")
    if changed:
        patch.write_bytes(PATCH.replace(b"approved", b"changed"))
    logs = config.logs / config.ticket_id
    logs.mkdir(parents=True)
    (logs / "error.log").write_text("synthetic failure\n", encoding="utf-8")
    bank = config.logs.parent / "bank.json"
    record_result(bank, 0)
    environment = dict(os.environ)
    environment.update({
        "HOME": str(config.logs.parent / "home"),
        "REPAIR_CHECKOUT": str(root), "REPAIR_WORK_CLONE": str(config.work_clone),
        "REPAIR_LOG_ROOT": str(config.logs), "REPAIR_PLAN_ROOT": str(config.plans),
        "REPAIR_STATE_ROOT": str(config.logs.parent / "state"), "REPAIR_BANK_STATE": str(bank),
        "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "Repair Test",
        "GIT_CONFIG_KEY_1": "user.email", "GIT_CONFIG_VALUE_1": "repair@example.invalid",
    })
    # When: a real Python process invokes the apply-approved CLI entry point.
    result = subprocess.run(
        (sys.executable, "-c", BOOTSTRAP, config.ticket_id), env=environment,
        capture_output=True, text=True, timeout=30, check=False,
    )
    # Then: a changed artifact is refused before cloning; approved bytes reach
    # the published repair branch and a visible PR result, without merging main.
    if changed:
        assert result.returncode != 0
        assert not config.work_clone.exists()
        assert git(root, "ls-remote", "--heads", "origin", "repair/t_abcdef") == ""
    else:
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["pr_url"] == "https://example.invalid/pull/1"
        assert payload["pr_error"] is None
        assert payload["phase"] == "completed"
        assert git(remote, "show", "repair/t_abcdef:automation/example.txt") == "approved"
        assert git(remote, "show", "main:automation/example.txt") == "old"
