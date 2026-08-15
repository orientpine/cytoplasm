"""Retiring a skill gate's pending record: consume-on-mount, and the audited abandon.

``skill_gate_request`` keeps EXACTLY ONE live request per gate key; nothing ever
retired one. A mounted deploy therefore left its decided record behind, and the
lifecycle guard (L3 — a request the owner already decided is never destroyed)
correctly turned that debris into a hard refusal on the NEXT deploy of the same
skill. The decision was consumed; only the file survived.

R1  Retirement is a compare-and-swap over the RAW stored fields. The record lives
    at ``pending/{record_name}.json``, so the skill IS the lookup key; the stored
    ``hash`` and ``message_id`` must still equal what was mounted. A record that
    moved on is left alone — never a blind delete.
R2  Every mutation happens under the SAME key lease the request path takes.
R3  ``consume`` and ``abandon`` never touch Discord. The owner's decision stays visible.
R4  ``consume`` never fails a mount that already succeeded: every outcome is a
    machine-readable token the pipeline logs instead of rolling back.
R5  ``abandon`` fsyncs its audit line BEFORE dropping the record, so a crash
    leaves a recoverable record rather than an unaudited deletion.
"""
from __future__ import annotations

import getpass
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from automation.interop.approval_lifecycle import (
    ApprovalRecordsError,
)
from automation.skill_gate_approval import SkillApprovalGate
from automation.skill_gate_request import lease
from automation.skill_gate_specs import mask

RETIREMENT_REFUSAL_EXIT: Final = 1
ABANDON_LOG_NAME: Final = "approval-abandons.jsonl"


class Retirement(StrEnum):
    """Every terminal state of a retirement attempt, as its machine-readable token."""

    CONSUMED = "consumed"
    ABANDONED = "abandoned"
    RECORD_ABSENT = "record-absent"
    RECORD_SUPERSEDED = "record-superseded"
    BINDING_MISMATCH = "binding-mismatch"
    STORE_UNREADABLE = "store-unreadable"
    LEASE_HELD = "lease-held"
    DROP_FAILED = "drop-failed"
    AUDIT_FAILED = "audit-failed"
    SUPERSEDE_FAILED = "supersede-failed"
    NOT_LEGACY = "not-legacy"


_BENIGN: Final = frozenset({Retirement.RECORD_ABSENT, Retirement.RECORD_SUPERSEDED})


@dataclass(frozen=True, slots=True)
class Retired:
    """One retirement outcome mapped onto the gate's stdout/exit contract."""

    outcome: Retirement
    exit_code: int
    message: str


@dataclass(frozen=True, slots=True)
class AbandonOrder:
    """The operator override: which live message to retire, why, and on whose authority."""

    message_id: str
    reason: str
    actor: str
    legacy_only: bool = False


def actor() -> str:
    """Who is overriding — the invoking human when the gate runs under ``sudo -u agent``."""
    return os.environ.get("SUDO_USER") or getpass.getuser()


def abandon_log(approval_log: Path) -> Path:
    """The override audit lives beside the approval log whose decisions it retires."""
    return approval_log.with_name(ABANDON_LOG_NAME)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _matched(gate: SkillApprovalGate, message_id: str) -> dict[str, str] | Retirement:
    """The stored record iff it STILL binds this exact (skill, hash, message id)."""
    try:
        record = gate.stored()
    except ApprovalRecordsError:
        return Retirement.STORE_UNREADABLE
    if record is None:
        return Retirement.RECORD_ABSENT
    if (record.get("hash", ""), record.get("message_id", "")) != (gate.spec.digest, message_id):
        return Retirement.RECORD_SUPERSEDED
    return record


def _token(gate: SkillApprovalGate, prefix: str, outcome: Retirement) -> str:
    return f"{prefix} skill={gate.spec.skill} reason={outcome.value}"


def _retired(gate: SkillApprovalGate, verb: str, message_id: str) -> str:
    return f"{verb} skill={gate.spec.skill} hash={gate.spec.digest} message_id={mask(message_id)}"


def _append_audit(path: Path, line: dict[str, str]) -> None:
    """Durable: the override reaches the disk before the record it retires disappears."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        _ = handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


def _noop(gate: SkillApprovalGate, outcome: Retirement) -> Retired:
    """A record that is absent or superseded is already retired — nothing to do, nothing wrong."""
    return Retired(outcome, 0, _token(gate, "CONSUME-NOOP", outcome))


def _failed(gate: SkillApprovalGate, outcome: Retirement) -> Retired:
    return Retired(outcome, RETIREMENT_REFUSAL_EXIT, _token(gate, "CONSUME-FAILED", outcome))


def _refused(gate: SkillApprovalGate, outcome: Retirement) -> Retired:
    return Retired(outcome, RETIREMENT_REFUSAL_EXIT, _token(gate, "ABANDON-REFUSED", outcome))


def consume(gate: SkillApprovalGate, message_id: str) -> Retired:
    """Retire the decision this mount consumed — CAS, never fatal to a successful mount."""
    with lease(gate.surface.gate_dir).hold(gate.spec.key()) as owned:
        if not owned:
            return _failed(gate, Retirement.LEASE_HELD)
        found = _matched(gate, message_id)
        if isinstance(found, Retirement):
            return _noop(gate, found) if found in _BENIGN else _failed(gate, found)
        try:
            gate.path().unlink(missing_ok=True)
        except OSError:
            return _failed(gate, Retirement.DROP_FAILED)
        return Retired(Retirement.CONSUMED, 0, _retired(gate, "CONSUMED", message_id))


def abandon(gate: SkillApprovalGate, order: AbandonOrder, audit_path: Path) -> Retired:
    """Operator override: refuses on ANY field mismatch, audits first, never deletes the message."""
    with lease(gate.surface.gate_dir).hold(gate.spec.key()) as owned:
        if not owned:
            return _refused(gate, Retirement.LEASE_HELD)
        found = _matched(gate, order.message_id)
        if isinstance(found, Retirement):
            mismatched = found is Retirement.RECORD_SUPERSEDED
            return _refused(gate, Retirement.BINDING_MISMATCH if mismatched else found)
        schema_fields = {"action_hash", "kind", "channel_id", "policy_version", "surface"}
        if order.legacy_only and schema_fields <= found.keys():
            return _refused(gate, Retirement.NOT_LEGACY)
        line = {
            "actor": order.actor,
            "event": "skill-gate-abandon",
            "hash": found["hash"],
            "key": gate.spec.key(),
            "message_id": found["message_id"],
            "reason": order.reason,
            "skill": gate.spec.skill,
            "timestamp": _utc_now(),
        }
        try:
            _append_audit(audit_path, line)
        except OSError:
            return _refused(gate, Retirement.AUDIT_FAILED)
        try:
            gate.path().unlink(missing_ok=True)
        except OSError:
            return _refused(gate, Retirement.DROP_FAILED)
        return Retired(Retirement.ABANDONED, 0, _retired(gate, "ABANDONED", order.message_id))


def emit(retired: Retired) -> int:
    """Both halves of the contract are machine-readable: success on stdout, refusal on stderr."""
    print(retired.message, file=sys.stdout if retired.exit_code == 0 else sys.stderr)
    return retired.exit_code
