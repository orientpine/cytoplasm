"""Approved CLI preflight tests; original lifecycle tests remain frozen."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.interop.approval_surface import ApprovalBinding, ApprovalKind, ApprovalSurface, POLICY_VERSION
from automation.repair import repair_ops_cli as cli
from automation.repair.repair_ops_approval import ManualOwnerApproval
from automation.repair.repair_ops_core import Approval, RepairOutcome, RepairPhase
from automation.repair.repair_ops_git import RepairOpsError
from automation.repair.repair_ops_pending import APPROVE_EMOJI, PendingRepairApproval, PendingRepairApprovalStore, approval_request_content
from automation.repair.repair_patch_binding import content_action_hash, load_patch_artifact
from tests.unit.test_repair_ops_residuals import OutcomeAgent, config_at
from tests.unit.test_repair_patch_apply_residuals import PATCH


@dataclass(frozen=True, slots=True)
class ApprovedTransport:
    pending: PendingRepairApproval
    owner_id: str = "owner"
    binding: ApprovalBinding = ApprovalBinding(ApprovalKind.REPAIR, ApprovalSurface.AGENT_CHAT_THREAD, "111", POLICY_VERSION)

    def for_pending(self, pending: PendingRepairApproval) -> ApprovedTransport:
        assert pending == self.pending
        return self

    def content(self, message_id: str) -> str:
        return approval_request_content(self.pending)

    def reaction_users(self, message_id: str, emoji: str) -> tuple[tuple[str, bool], ...]:
        return ((self.owner_id, False),) if emoji == APPROVE_EMOJI else ()


@pytest.fixture
def approved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[cli.RepairOpsConfig, PendingRepairApproval]:
    config = config_at(tmp_path)
    patch = config.plans / config.ticket_id / "patch.diff"
    patch.parent.mkdir(parents=True)
    patch.write_bytes(PATCH)
    artifact = load_patch_artifact(patch)
    pending = PendingRepairApproval(
        config.ticket_id, patch.name,
        content_action_hash(config.ticket_id, patch.name, artifact.patch_sha256, artifact.changes),
        "nonce", "message", datetime(2026, 9, 4, tzinfo=UTC),
        2, artifact.patch_sha256, artifact.changes, str(patch),
    )
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(pending)
    monkeypatch.setenv("REPAIR_APPROVAL_PENDING_ROOT", str(store.root))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.setattr(cli, "configured_discord", lambda: ApprovedTransport(pending))
    return config, pending


def test_apply_refuses_before_agent_when_approved_content_changed(
    approved: tuple[cli.RepairOpsConfig, PendingRepairApproval], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a genuine owner reaction but a replacement patch on disk.
    config, pending = approved
    assert pending.patch_source_path is not None
    Path(pending.patch_source_path).write_bytes(PATCH.replace(b"approved", b"changed"))
    monkeypatch.setattr(cli, "_agent", lambda *args: pytest.fail("planner/sandbox must not start"))
    # When / Then: the cheap guard rejects with the existing content-refusal error.
    with pytest.raises(RepairOpsError, match="does not bind"):
        cli._apply_approved(config)


def test_apply_returns_waiting_when_cheap_approval_denies(
    approved: tuple[cli.RepairOpsConfig, PendingRepairApproval], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a gate denial that must not reach the expensive agent.
    config, _ = approved
    monkeypatch.setattr(ManualOwnerApproval, "permits", lambda *args: False)
    monkeypatch.setattr(cli, "_agent", lambda *args: pytest.fail("planner/sandbox must not start"))
    # When: apply-approved checks approval before constructing an agent.
    code = cli._apply_approved(config)
    # Then: denial has the same waiting exit contract as the lifecycle.
    assert code == 5


def test_apply_threads_stored_digest_when_owner_approved_current_content(
    approved: tuple[cli.RepairOpsConfig, PendingRepairApproval], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid record and a current patch with a real content-bound approval.
    config, pending = approved
    digests: list[str | None] = []

    def agent(config: cli.RepairOpsConfig, approval: Approval, digest: str | None) -> OutcomeAgent:
        digests.append(digest)
        return OutcomeAgent(RepairOutcome(RepairPhase.AWAITING_APPROVAL, config.ticket_id, None, None))

    monkeypatch.setattr(cli, "_agent", agent)
    monkeypatch.setattr(cli, "private_log", lambda *args: "")
    # When: the CLI forwards the authorized run to the agent factory.
    cli._apply_approved(config)
    # Then: the final repository boundary receives the stored digest, not a fresh one.
    assert digests == [pending.patch_sha256]
