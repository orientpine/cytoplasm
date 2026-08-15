"""Owner-gated repair orchestration for W6-2 tickets."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from automation.repair.repair_redaction import redact
from automation.repair.repair_lifecycle import LifecycleState, RepairLifecycleStore


class RepairPhase(StrEnum):
    """The persistent, externally safe lifecycle of one repair ticket."""

    AWAITING_APPROVAL = "awaiting_approval"
    SANDBOX_REJECTED = "sandbox_rejected"
    BANK_BLOCKED = "bank_blocked"
    COMPLETED = "completed"
    REOPENED = "reopened"


@dataclass(frozen=True, slots=True)
class RepairPlan:
    """A redacted diagnosis and a repository-only patch proposal."""

    ticket_id: str
    diagnosis: str
    repro_path: Path
    patch_path: Path
    bank_scenario_path: Path | None = None


@dataclass(frozen=True, slots=True)
class SandboxVerdict:
    """The two non-negotiable checks performed by the peer sandbox."""

    existing_bank_passed: bool
    repro_green: bool
    existing_bank_returncode: int = -1
    existing_bank_stdout_tail: str = ""
    existing_bank_stderr_tail: str = ""
    repro_returncode: int = -1
    repro_stdout_tail: str = ""
    repro_stderr_tail: str = ""

    def sandbox_checks(self) -> str:
        bank = (
            f"bank rc={self.existing_bank_returncode} "
            f"stdout_tail={self.existing_bank_stdout_tail!r} "
            f"stderr_tail={self.existing_bank_stderr_tail!r}"
        )
        repro = (
            f"repro rc={self.repro_returncode} "
            f"stdout_tail={self.repro_stdout_tail!r} "
            f"stderr_tail={self.repro_stderr_tail!r}"
        )
        return f"{bank} | {repro}"


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    """Safe summary of a repair run; raw logs never cross this boundary."""

    phase: RepairPhase
    ticket_id: str
    commit: str | None
    patch_doc: Path | None


class Planner(Protocol):
    """Premium diagnosis and patch proposal boundary."""

    def plan(self, ticket_id: str, private_log: str) -> RepairPlan: ...


class Sandbox(Protocol):
    """Peer-only validation boundary."""

    def validate(self, plan: RepairPlan) -> SandboxVerdict: ...


class Approval(Protocol):
    """Owner-bound approval boundary."""

    def permits(self, ticket_id: str, patch_path: Path) -> bool: ...


class Repository(Protocol):
    """Repository-only mutation boundary."""

    def apply(self, patch_path: Path) -> str: ...

    def register_bank(self, scenario_path: Path) -> str | None: ...

    def bank_passes(self, scenario_path: Path | None) -> bool: ...

    def bank_allows_apply(self) -> bool: ...

    def revert(self, commit: str) -> None: ...


class TicketBoard(Protocol):
    """Minimal W6-1 ticket mutation boundary."""

    def complete(self, ticket_id: str, summary: str) -> None: ...

    def reopen(self, ticket_id: str, summary: str) -> None: ...


class PatchDocs(Protocol):
    """Redacted patch-document boundary."""

    def write(self, plan: RepairPlan, commit: str) -> Path: ...


@dataclass(frozen=True, slots=True)
class RepairAgent:
    """Drive a W6-1 ticket through sandbox, approval, apply, and rollback."""

    planner: Planner
    sandbox: Sandbox
    approval: Approval
    repository: Repository
    tickets: TicketBoard
    patch_docs: PatchDocs
    lifecycle: RepairLifecycleStore | None = None

    def repair(self, ticket_id: str, private_log: str) -> RepairOutcome:
        """Repair only after peer validation and an owner-bound approval."""
        self._state(ticket_id, LifecycleState.OPEN)
        self._state(ticket_id, LifecycleState.DIAGNOSING)
        plan = self.planner.plan(ticket_id, private_log)
        verdict = self.sandbox.validate(plan)
        self._state(ticket_id, LifecycleState.SANDBOXED, verdict.sandbox_checks())
        if not verdict.existing_bank_passed or not verdict.repro_green:
            self._state(ticket_id, LifecycleState.REOPENED, "sandbox gate rejected")
            self.tickets.reopen(ticket_id, "sandbox gate rejected; no patch applied")
            return RepairOutcome(RepairPhase.SANDBOX_REJECTED, ticket_id, None, None)
        if not self.repository.bank_allows_apply():
            self.tickets.reopen(ticket_id, "regression bank state is red; no patch applied")
            return RepairOutcome(RepairPhase.BANK_BLOCKED, ticket_id, None, None)
        if not self.approval.permits(ticket_id, plan.patch_path):
            self._state(ticket_id, LifecycleState.AWAITING_APPROVAL)
            return RepairOutcome(RepairPhase.AWAITING_APPROVAL, ticket_id, None, None)
        commit = self.repository.apply(plan.patch_path)
        self._state(ticket_id, LifecycleState.APPLIED, commit)
        if plan.bank_scenario_path is not None:
            registered_commit = self.repository.register_bank(plan.bank_scenario_path)
            if registered_commit is not None:
                commit = registered_commit
        if not self.repository.bank_passes(plan.bank_scenario_path):
            self.repository.revert(commit)
            self._state(ticket_id, LifecycleState.REOPENED, "regression bank failed; patch reverted")
            self.tickets.reopen(ticket_id, "regression bank failed; patch reverted")
            return RepairOutcome(RepairPhase.REOPENED, ticket_id, commit, None)
        patch_doc = self.patch_docs.write(plan, commit)
        self._state(ticket_id, LifecycleState.DONE, commit)
        self.tickets.complete(ticket_id, f"repair applied {commit}; {patch_doc.name}")
        return RepairOutcome(RepairPhase.COMPLETED, ticket_id, commit, patch_doc)

    def _state(self, ticket_id: str, state: LifecycleState, reason: str = "") -> None:
        if self.lifecycle is not None:
            _ = self.lifecycle.transition(ticket_id, state, reason)


def safe_diagnosis(raw_log: str) -> str:
    """Create a log-derived diagnosis that is safe to store in repository artifacts."""
    return redact(raw_log).replace("\n", " ")[:240]
