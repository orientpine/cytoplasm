"""One guarded approval request, shared by the deploy and publish skill gates.

The PENDING fast path reuses an unchanged live request, ``--fresh`` supersedes it
(DELETE before the replacement POST), and every other outcome is a terminal
refusal carrying a machine-readable ``reason=`` token plus
:data:`LIFECYCLE_REFUSAL_EXIT` — never 3, which the deploy pipeline already reads
as "weekly auto-proposal rate limit".
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Protocol, assert_never

from automation.interop.approval_lease import ApprovalLease, FileKeyLease, PostingJournal
from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalGate,
    ApprovalRecordsError,
    ApprovalSurfaceError,
    Outcome,
    PostedApproval,
    Reason,
    Verdict,
    request_owner_approval,
)
from automation.interop.approval_types import ApprovalRequest, Probe
from automation.skill_gate_approval import SkillApprovalGate
from automation.skill_gate_specs import DeploySpec, GateSpec, PublishSpec

LIFECYCLE_REFUSAL_EXIT: Final = 6
LEASE_DIRNAME: Final = "approval-leases"
JOURNAL_DIRNAME: Final = "posting-journal"


@dataclass(frozen=True, slots=True)
class Requested:
    """One request outcome mapped onto the gate's legacy stdout/exit contract."""

    record: dict[str, str] | None
    exit_code: int
    message: str = ""
    posted: bool = False


class GateRoot(Protocol):
    @property
    def gate_dir(self) -> Path: ...


class RequestGate(ApprovalGate, Protocol):
    """The lifecycle plus the record/spec access needed by its CLI adapter."""

    @property
    def surface(self) -> GateRoot: ...

    @property
    def spec(self) -> GateSpec: ...

    def stored(self) -> dict[str, str] | None: ...

    def channel_id(self) -> str: ...

    def new_record(self, posted: PostedApproval) -> dict[str, str]: ...


def lease(gate_dir: Path) -> FileKeyLease:
    return FileKeyLease(gate_dir / LEASE_DIRNAME)


def journal(gate_dir: Path) -> PostingJournal:
    return PostingJournal(gate_dir / JOURNAL_DIRNAME)


def _refused(reason: Reason, outcome: str = "refused") -> Requested:
    message = f"REFUSED: approval request not posted outcome={outcome} reason={reason.value}"
    return Requested(None, LIFECYCLE_REFUSAL_EXIT, message)


def _clear(gate: RequestGate, request: ApprovalRequest) -> Reason | None:
    """Destroy one outstanding request, or name the reason it must survive untouched."""
    try:
        state = gate.probe(request)
    except (ApprovalSurfaceError, OSError):
        return Reason.UNVERIFIABLE
    match state:
        case Probe.APPROVED | Probe.CANCELLED:
            return Reason.OWNER_DECIDED
        case Probe.UNVERIFIABLE:
            return Reason.UNVERIFIABLE
        case Probe.BINDING_MISMATCH:
            return Reason.BINDING_MISMATCH
        case Probe.MISSING:
            gate.drop(request)
            return None
        case Probe.BOUND_PENDING:
            try:
                gate.delete(request)
            except (ApprovalSurfaceError, OSError):
                return Reason.SUPERSEDE_FAILED
            gate.drop(request)
            return None
        case unreachable:
            assert_never(unreachable)


def supersede(gate: RequestGate, key: str, held: ApprovalLease) -> Reason | None:
    """``--fresh``: destroy the live request BEFORE a new one is posted — never orphan it."""
    with held.hold(key) as owned:
        if not owned:
            return Reason.LEASE_HELD
        try:
            outstanding = gate.outstanding(key)
        except ApprovalRecordsError:
            return Reason.STORE_UNREADABLE
        for request in outstanding:
            refusal = _clear(gate, request)
            if refusal is not None:
                return refusal
    return None


def settle(verdict: Verdict, gate: RequestGate) -> Requested:
    """Map one lifecycle verdict onto the gate's stdout record and exit code."""
    match verdict.outcome:
        case Outcome.POSTED:
            posted = verdict.posted
            if posted is None:
                return _refused(Reason.SUPERSEDE_FAILED, verdict.outcome.value)
            return Requested(gate.new_record(posted), 0, posted=True)
        case Outcome.PENDING:
            try:
                record = gate.stored()
            except ApprovalRecordsError:
                return _refused(Reason.STORE_UNREADABLE, verdict.outcome.value)
            if record is None:
                return _refused(Reason.MESSAGE_MISSING, verdict.outcome.value)
            return Requested(record, 0)
        case Outcome.DEFERRED | Outcome.REFUSED:
            return _refused(verdict.reason or Reason.UNVERIFIABLE, verdict.outcome.value)
        case unreachable:
            assert_never(unreachable)


def reuse(gate: RequestGate) -> Requested | None:
    """PENDING fast path — the identical live request is reused; an unreadable record refuses."""
    try:
        record = gate.stored()
    except ApprovalRecordsError:
        return _refused(Reason.STORE_UNREADABLE)
    if record is None:
        return None
    found = gate.spec.stored(record)
    if found is None:
        return _refused(Reason.STORE_UNREADABLE)
    return Requested(record, 0) if found.action_hash == gate.spec.action_hash() else None


def _prepare_card(gate: RequestGate) -> RequestGate | Requested:
    """재사용 뒤, 저널·삭제·게시 전 최종 본문을 한 번만 확정한다."""
    if not isinstance(gate.spec, DeploySpec | PublishSpec):
        return gate
    if not isinstance(gate, SkillApprovalGate):
        return _refused(Reason.UNVERIFIABLE)
    for version in (3, 2, 1):
        spec = replace(gate.spec, render_version=version)
        try:
            content = spec.render()
        except (ImportError, RuntimeError, TypeError, ValueError) as error:
            marker = "APPROVAL-RENDER-REFUSED" if version == 1 else "APPROVAL-RENDER-FALLBACK"
            print(f"{marker}: {type(error).__name__}", file=sys.stderr)
            continue
        if len(content) > 1900:
            return _refused(Reason.UNVERIFIABLE)
        return replace(gate, spec=replace(spec, posted_text=content))
    return _refused(Reason.UNVERIFIABLE)


def post_request(gate: RequestGate, *, fresh: bool) -> Requested:
    """One guarded post: the optional ``--fresh`` supersede, then the shared lifecycle."""
    prepared = _prepare_card(gate)
    if isinstance(prepared, Requested):
        return prepared
    gate = prepared
    key = gate.spec.key()
    held = lease(gate.surface.gate_dir)
    if fresh:
        refusal = supersede(gate, key, held)
        if refusal is not None:
            return _refused(refusal)
    intent = ApprovalIntent(
        key=key, action_hash=gate.spec.action_hash(), channel_id=gate.channel_id()
    )
    return settle(request_owner_approval(intent, gate, held, journal(gate.surface.gate_dir)), gate)


def emit(requested: Requested, *, json_output: bool) -> int:
    """Legacy stdout contract: the bare message id, or the sorted-key record with --json."""
    record = requested.record
    if record is None:
        print(requested.message, file=sys.stderr)
        return requested.exit_code
    print(json.dumps(record, sort_keys=True) if json_output else record["message_id"])
    return requested.exit_code
