from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from automation.interop.injection_adapter import InboundEvent, sign_event
from automation.regression_bank.bank_state import record_result
from automation.repair.repair_ops_adapters import ApprovalReaction, GitRepository, ManualOwnerApproval, PeerSandbox, SignedOwnerApproval
from automation.repair.repair_ops_core import RepairAgent, RepairOutcome, RepairPhase, RepairPlan, SandboxVerdict
from automation.repair.repair_lifecycle import LifecycleState, RepairLifecycleStore
from automation.repair.repair_ops_reporting import PatchDocumentWriter


@dataclass
class FakePlanner:
    plan_value: RepairPlan

    def plan(self, ticket_id: str, private_log: str) -> RepairPlan:
        del private_log
        assert ticket_id == self.plan_value.ticket_id
        return self.plan_value


@dataclass
class FakeSandbox:
    verdict: SandboxVerdict

    def validate(self, plan: RepairPlan) -> SandboxVerdict:
        del plan
        return self.verdict


@dataclass
class FakeApproval:
    permitted: bool

    def permits(self, ticket_id: str, patch_path: Path) -> bool:
        del ticket_id, patch_path
        return self.permitted


@dataclass
class FakeRepository:
    bank_ok: bool
    bank_gate_ok: bool = True
    applied: list[Path] = field(default_factory=list)
    registered: list[Path] = field(default_factory=list)
    bank_scenarios: list[Path | None] = field(default_factory=list)
    reverted: list[str] = field(default_factory=list)

    def apply(self, patch_path: Path) -> str:
        self.applied.append(patch_path)
        return "abc123"

    def register_bank(self, scenario_path: Path) -> str | None:
        self.registered.append(scenario_path)
        return None

    def bank_passes(self, scenario_path: Path | None = None) -> bool:
        self.bank_scenarios.append(scenario_path)
        return self.bank_ok

    def bank_allows_apply(self) -> bool:
        return self.bank_gate_ok

    def revert(self, commit: str) -> None:
        self.reverted.append(commit)


@dataclass
class FakeTickets:
    completed: list[str] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)

    def complete(self, ticket_id: str, summary: str) -> None:
        del summary
        self.completed.append(ticket_id)

    def reopen(self, ticket_id: str, summary: str) -> None:
        del summary
        self.reopened.append(ticket_id)


@dataclass
class FakeDocs:
    path: Path

    def write(self, plan: RepairPlan, commit: str) -> Path:
        del plan, commit
        return self.path


def agent_for(
    tmp_path: Path, verdict: SandboxVerdict, permitted: bool, bank_ok: bool, bank_gate_ok: bool = True
) -> tuple[RepairAgent, FakeRepository, FakeTickets]:
    plan = RepairPlan(
        "t_repair01",
        "diagnosis",
        tmp_path / "repro.sh",
        tmp_path / "patch.diff",
        tmp_path / "scenario.yaml",
    )
    repository = FakeRepository(bank_ok, bank_gate_ok)
    tickets = FakeTickets()
    return RepairAgent(FakePlanner(plan), FakeSandbox(verdict), FakeApproval(permitted), repository, tickets, FakeDocs(tmp_path / "patch.md")), repository, tickets


def test_repair_when_peer_bank_fails_then_reopens_without_apply(tmp_path: Path) -> None:
    # Given: the existing peer bank is red.
    agent, repository, tickets = agent_for(tmp_path, SandboxVerdict(False, True), True, True)

    # When: ops attempts the repair.
    outcome = agent.repair("t_repair01", "private log")

    # Then: no repository operation happens and the ticket is reopened.
    assert outcome.phase is RepairPhase.SANDBOX_REJECTED
    assert repository.applied == []
    assert tickets.reopened == ["t_repair01"]


def test_repair_when_owner_approval_is_missing_then_waits_without_apply(tmp_path: Path) -> None:
    # Given: peer validation is green but approval is absent.
    agent, repository, tickets = agent_for(tmp_path, SandboxVerdict(True, True), False, True)

    # When: ops attempts the repair.
    outcome = agent.repair("t_repair01", "private log")

    # Then: the patch remains unapplied and the ticket stays pending.
    assert outcome.phase is RepairPhase.AWAITING_APPROVAL
    assert repository.applied == []
    assert tickets.completed == []


def test_repair_when_sandbox_checks_complete_then_persists_masked_evidence(tmp_path: Path) -> None:
    # Given: green sandbox checks whose raw output includes a token-shaped fixture.
    plan = RepairPlan("t_repair01", "diagnosis", tmp_path / "repro.sh", tmp_path / "patch.diff")
    verdict = SandboxVerdict(
        existing_bank_passed=True,
        repro_green=True,
        existing_bank_returncode=0,
        existing_bank_stdout_tail="BANK-CASE-VERDICT: ALL PASS",
        existing_bank_stderr_tail="",
        repro_returncode=0,
        repro_stdout_tail="reproduction repaired",
        repro_stderr_tail="sk-repair-fixture-token",
    )
    lifecycle = RepairLifecycleStore(tmp_path / "lifecycle")
    agent = RepairAgent(
        FakePlanner(plan),
        FakeSandbox(verdict),
        FakeApproval(False),
        FakeRepository(True),
        FakeTickets(),
        FakeDocs(tmp_path / "patch.md"),
        lifecycle,
    )

    # When: the green repair waits for a future owner approval.
    outcome = agent.repair("t_repair01", "private log")

    # Then: the durable current record retains both masked check tails and return codes.
    record = lifecycle.read("t_repair01")
    assert outcome.phase is RepairPhase.AWAITING_APPROVAL
    assert record.state is LifecycleState.AWAITING_APPROVAL
    assert "bank rc=0" in record.sandbox_checks
    assert "repro rc=0" in record.sandbox_checks
    assert "BANK-CASE-VERDICT: ALL PASS" in record.sandbox_checks
    assert "[MASKED_KEY]" in record.sandbox_checks
    assert "sk-repair-fixture-token" not in record.sandbox_checks


def test_peer_sandbox_when_building_staged_command_then_chains_shell_statements(tmp_path: Path) -> None:
    # Given: a staged patch check with a temporary checkout.
    sandbox = PeerSandbox("example-primary-node", tmp_path)

    # When: the sandbox builds its shell command.
    command = sandbox.staged_command(tmp_path / "patch.diff", "true")

    # Then: trap installation completes before the next shell statement begins.
    assert "trap 'rm -rf \"$sandbox\"' EXIT && mkdir \"$sandbox/home\"" in command


def test_repair_when_recorded_bank_state_is_red_then_blocks_without_apply(tmp_path: Path) -> None:
    # Given: peer validation is green but the persisted weekly bank state is red.
    agent, repository, tickets = agent_for(tmp_path, SandboxVerdict(True, True), True, True, False)

    # When: the approved repair reaches the W6-2 apply decision.
    outcome = agent.repair("t_repair01", "private log")

    # Then: no patch is applied and the ticket reopens with a distinct blocked phase.
    assert outcome.phase is RepairPhase.BANK_BLOCKED
    assert repository.applied == []
    assert tickets.reopened == ["t_repair01"]


def test_repository_when_recorded_bank_state_is_red_then_refuses_apply(tmp_path: Path) -> None:
    # Given: W6-2's concrete Git adapter is bound to a failed weekly-bank state file.
    state_path = tmp_path / "regression-bank-state.json"
    _ = record_result(state_path, 1)
    repository = GitRepository(tmp_path, state_path)

    # When: the W6-2 pre-apply gate reads that shared state.
    allowed = repository.bank_allows_apply()

    # Then: it rejects mutation before GitRepository.apply() can run.
    assert allowed is False


def test_repair_when_post_apply_bank_fails_then_reverts_and_reopens(tmp_path: Path) -> None:
    # Given: the peer is green but the live regression check will fail.
    agent, repository, tickets = agent_for(tmp_path, SandboxVerdict(True, True), True, False)

    # When: the approved repair reaches apply.
    outcome = agent.repair("t_repair01", "private log")

    # Then: a real repository revert is requested and the ticket reopens.
    assert outcome.phase is RepairPhase.REOPENED
    assert repository.reverted == ["abc123"]
    assert tickets.reopened == ["t_repair01"]


def test_repair_when_all_gates_pass_then_applies_registers_and_completes(tmp_path: Path) -> None:
    # Given: peer validation, owner approval, and the live bank are green.
    agent, repository, tickets = agent_for(tmp_path, SandboxVerdict(True, True), True, True)

    # When: ops repairs the ticket.
    outcome = agent.repair("t_repair01", "private log")

    # Then: the patch and scenario are registered before ticket completion.
    assert outcome == RepairOutcome(RepairPhase.COMPLETED, "t_repair01", "abc123", tmp_path / "patch.md")
    assert len(repository.applied) == 1
    assert len(repository.registered) == 1
    assert repository.bank_scenarios == [tmp_path / "scenario.yaml"]
    assert tickets.completed == ["t_repair01"]


def test_manual_approval_when_reactor_is_bot_then_rejects(tmp_path: Path) -> None:
    # Given: a bot reaction uses otherwise valid owner approval text.
    patch = tmp_path / "patch.diff"
    action_hash = hashlib.sha256(b"repair:t_repair01:patch.diff").hexdigest()
    gate = ManualOwnerApproval("cha", ApprovalReaction("m1", "cha", "approvals", f"APPROVE repair {action_hash} ticket:t_repair01", True))

    # When: it is checked at the apply gate.
    approved = gate.permits("t_repair01", patch)

    # Then: the bot cannot approve a repair.
    assert approved is False


def test_signed_injection_when_owner_hmac_matches_then_writes_approval_record(tmp_path: Path) -> None:
    # Given: a per-run HMAC binds the owner to this exact repair.
    patch = tmp_path / "patch.diff"
    action_hash = hashlib.sha256(b"repair:t_repair01:patch.diff").hexdigest()
    secret = b"1" * 32
    event = InboundEvent("e1", "cha", "approvals", f"APPROVE repair {action_hash} ticket:t_repair01")
    gate = SignedOwnerApproval("cha", tmp_path / "approvals.jsonl", event, sign_event(event, secret), secret, True)

    # When: the isolated E2E path checks the event.
    approved = gate.permits("t_repair01", patch)

    # Then: the owner approval is accepted and recorded with restrictive mode.
    assert approved is True
    assert '"method":"signed_injection_e2e"' in (tmp_path / "approvals.jsonl").read_text(encoding="utf-8")
    assert (tmp_path / "approvals.jsonl").stat().st_mode & 0o777 == 0o600


def test_patch_document_when_diagnosis_contains_fixture_then_redacts_it(tmp_path: Path) -> None:
    # Given: a diagnosis contains a composed sensitive fixture value.
    fixture = "".join(("sk-", "repair", "-fixture"))
    plan = RepairPlan("t_repair01", f"failure {fixture}", tmp_path / "repro.sh", tmp_path / "patch.diff")

    # When: the patch note is written.
    document = PatchDocumentWriter(tmp_path).write(plan, "abc123")

    # Then: the fixture never reaches the repository document.
    assert fixture not in document.read_text(encoding="utf-8")
