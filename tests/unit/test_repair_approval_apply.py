from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


from automation.repair.repair_ops_approval import ApprovalReaction, ManualOwnerApproval
from automation.repair.repair_ops_core import RepairAgent, RepairPhase, RepairPlan, SandboxVerdict
from automation.repair.repair_patch_binding import content_action_hash, load_patch_artifact


OWNER_ID = "280680578314010625"


@dataclass
class FakePlanner:
    plan_value: RepairPlan

    def plan(self, ticket_id: str, private_log: str) -> RepairPlan:
        del private_log
        assert ticket_id == self.plan_value.ticket_id
        return self.plan_value


@dataclass
class FakeSandbox:
    def validate(self, plan: RepairPlan) -> SandboxVerdict:
        del plan
        return SandboxVerdict(True, True)


@dataclass
class FakeRepository:
    applied: list[Path] = field(default_factory=list)
    registered: list[Path] = field(default_factory=list)

    def apply(self, patch_path: Path) -> str:
        self.applied.append(patch_path)
        return "repair-commit"

    def register_bank(self, scenario_path: Path) -> str | None:
        self.registered.append(scenario_path)
        return None

    def bank_passes(self, scenario_path: Path | None) -> bool:
        del scenario_path
        return True

    def bank_allows_apply(self) -> bool:
        return True

    def revert(self, commit: str) -> None:
        del commit


@dataclass
class FakeTickets:
    completed: list[str] = field(default_factory=list)

    def complete(self, ticket_id: str, summary: str) -> None:
        del summary
        self.completed.append(ticket_id)

    def reopen(self, ticket_id: str, summary: str) -> None:
        del ticket_id, summary


@dataclass
class FakePatchDocs:
    document: Path

    def write(self, plan: RepairPlan, commit: str) -> Path:
        del plan, commit
        return self.document


def test_approved_manual_reaction_when_sandbox_and_bank_are_green_then_applies_registers_and_documents(tmp_path: Path) -> None:
    # Given: the watcher has converted cha's bound ✅ into the existing ManualOwnerApproval.
    patch = tmp_path / "patch.diff"
    _ = patch.write_text(
        "diff --git a/automation/mod.py b/automation/mod.py\n"
        "--- a/automation/mod.py\n"
        "+++ b/automation/mod.py\n"
        "@@ -1,2 +1,2 @@\n context\n-old\n+new\n",
        encoding="utf-8",
    )
    artifact = load_patch_artifact(patch)
    action_hash = content_action_hash("t-repair-1", patch.name, artifact.patch_sha256, artifact.changes)
    plan = RepairPlan("t-repair-1", "redacted diagnosis", tmp_path / "repro.sh", patch, tmp_path / "scenario.yaml")
    repository = FakeRepository()
    tickets = FakeTickets()
    agent = RepairAgent(
        FakePlanner(plan),
        FakeSandbox(),
        ManualOwnerApproval(OWNER_ID, ApprovalReaction("approval-message-1", OWNER_ID, "approvals", f"APPROVE repair {action_hash} ticket:t-repair-1", False)),
        repository,
        tickets,
        FakePatchDocs(tmp_path / "docs" / "patch.md"),
    )

    # When: the existing lifecycle processes that approval.
    outcome = agent.repair("t-repair-1", "private repair log")

    # Then: it uses the existing apply → bank +1 → redacted patch-doc completion path.
    assert outcome.phase is RepairPhase.COMPLETED
    assert repository.applied == [patch]
    assert repository.registered == [tmp_path / "scenario.yaml"]
    assert tickets.completed == ["t-repair-1"]
