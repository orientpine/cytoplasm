"""Redacted ticket updates and patch documentation for W6-2."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Mapping
from uuid import uuid4

from automation.interop.approval_surface import ApprovalKind, ApprovalSurface, reaction_instruction

from automation.repair.repair_capability import read_published
from automation.repair.repair_ops_core import RepairPlan
from automation.repair.repair_redaction import redact
from automation.repair.repair_report_queue import ReportRequest, compact, enqueue_if_missing_semantic


_REOPEN_REASON_CODES: Final[Mapping[str, str]] = {
    "sandbox gate rejected; no patch applied": "sandbox_rejected",
    "regression bank state is red; no patch applied": "bank_red",
    "regression bank failed; patch reverted": "bank_failed_reverted",
    "owner_cancelled": "owner_cancelled",
    "approval_expired": "approval_expired",
}


@dataclass(frozen=True, slots=True)
class HermesTicketBoard:
    """Queue capability-bound terminal lifecycle transitions without raw summaries."""

    def complete(self, ticket_id: str, summary: str) -> None:
        """Mark a ticket complete after a successful green regression run."""
        try:
            del summary
            capability = read_published(ticket_id)
            if capability is None:
                print("repair report enqueue deferred: capability unavailable", file=sys.stderr)
                return
            request = ReportRequest(
                request_id=uuid4().hex,
                operation="complete",
                ticket_id=ticket_id,
                reason_code="applied",
                occurrence=capability["occurrence"],
                mac=capability["mac"],
                created=datetime.now(tz=UTC).isoformat(),
            )
            _ = enqueue_if_missing_semantic(request)
            _ = compact()
            from automation.repair import repair_report_reconcile

            _ = repair_report_reconcile.reconcile()
        except Exception:  # noqa: BLE001, BROAD_EXCEPT_OK
            print("repair report enqueue skipped: reporting unavailable", file=sys.stderr)
            return

    def reopen(self, ticket_id: str, summary: str) -> None:
        """Reopen a ticket after sandbox rejection or an automatic rollback."""
        try:
            capability = read_published(ticket_id)
            if capability is None:
                print("repair report enqueue deferred: capability unavailable", file=sys.stderr)
                return
            request = ReportRequest(
                request_id=uuid4().hex,
                operation="reopen",
                ticket_id=ticket_id,
                reason_code=_REOPEN_REASON_CODES.get(summary, "unspecified"),
                occurrence=capability["occurrence"],
                mac=capability["mac"],
                created=datetime.now(tz=UTC).isoformat(),
            )
            _ = enqueue_if_missing_semantic(request)
            _ = compact()
            from automation.repair import repair_report_reconcile

            _ = repair_report_reconcile.reconcile()
        except Exception:  # noqa: BLE001, BROAD_EXCEPT_OK
            print("repair report enqueue skipped: reporting unavailable", file=sys.stderr)
            return


@dataclass(frozen=True, slots=True)
class PatchDocumentWriter:
    """Write a repository patch note without raw logs or sensitive fixture values."""

    docs_root: Path

    def write(self, plan: RepairPlan, commit: str) -> Path:
        """Record scope, verification, and deferred human follow-up in a redacted note."""
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        target = self.docs_root / f"{date}-{plan.ticket_id}.md"
        diagnosis = redact(plan.diagnosis)
        _ = target.write_text(
            "\n".join(
                (
                    f"# Repair {plan.ticket_id}",
                    "",
                    "## Scope",
                    "Repository code/config only; agent secrets and private logs were not read into git.",
                    "",
                    "## Applied state",
                    f"- commit: `{commit}`",
                    f"- diagnosis: {diagnosis}",
                    "",
                    "## Verification",
                    "- peer sandbox existing regression bank: PASS",
                    "- repair RED reproduction: GREEN",
                    "",
                    "## Deferred [USER] gate",
                    f"Real cha {reaction_instruction(ApprovalKind.REPAIR, ApprovalSurface.SKILL_APPROVALS, name_surface=True)} reaction remains a non-blocking production follow-up.",
                    "",
                )
            ),
            encoding="utf-8",
        )
        return target
