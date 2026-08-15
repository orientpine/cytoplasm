"""Shared owner-approval lifecycle façade.

L1  All mutation happens while this process holds the key's lease.
L2  A stored message id is never replaced — only superseded (delete BEFORE drop)
    or left alone. There is no code path that overwrites a message id.
L3  A request the owner has ALREADY decided (✅/⛔) is never destroyed — it is
    deferred so the watcher can consume it.
L4  Liveness that cannot be proven counts as live. Bindings that do not match
    are refused, never dropped.
L5  Every terminal non-success carries a machine-readable reason and exits non-zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, assert_never

from automation.interop.approval_lease import ApprovalLease, PostingJournal


class ApprovalRecordsError(RuntimeError):
    pass


class ApprovalSurfaceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    key: str
    action_hash: str
    message_id: str
    channel_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ApprovalIntent:
    key: str
    action_hash: str
    channel_id: str


class Probe(StrEnum):
    BOUND_PENDING = "bound-pending"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    MISSING = "missing"
    BINDING_MISMATCH = "binding-mismatch"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True, slots=True)
class PostedApproval:
    message_id: str
    channel_id: str


class ApprovalGate(Protocol):
    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]: ...

    def probe(self, request: ApprovalRequest) -> Probe: ...

    def delete(self, request: ApprovalRequest) -> None: ...

    def drop(self, request: ApprovalRequest) -> None: ...

    def post(self, intent: ApprovalIntent) -> PostedApproval: ...

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None: ...


class Outcome(StrEnum):
    POSTED = "posted"
    PENDING = "pending"
    DEFERRED = "deferred"
    REFUSED = "refused"


class Reason(StrEnum):
    LEASE_HELD = "lease-held"
    OWNER_DECIDED = "owner-decided"
    UNVERIFIABLE = "unverifiable"
    BINDING_MISMATCH = "binding-mismatch"
    STORE_UNREADABLE = "store-unreadable"
    SUPERSEDE_FAILED = "supersede-failed"
    POSTING_JOURNAL_STALE = "posting-journal-stale"
    CONTENT_CHANGED = "content-changed"
    MESSAGE_MISSING = "message-missing"
    DUPLICATE_COLLAPSED = "duplicate-collapsed"


@dataclass(frozen=True, slots=True)
class Cleared:
    request: ApprovalRequest
    reason: Reason


@dataclass(frozen=True, slots=True)
class Verdict:
    outcome: Outcome
    reason: Reason | None = None
    live: ApprovalRequest | None = None
    posted: PostedApproval | None = None
    cleared: tuple[Cleared, ...] = ()
    blocked: tuple[ApprovalRequest, ...] = ()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _probe(gate: ApprovalGate, request: ApprovalRequest) -> Probe:
    try:
        return gate.probe(request)
    except (ApprovalSurfaceError, OSError):
        return Probe.UNVERIFIABLE


def _destroy(
    gate: ApprovalGate,
    doomed: tuple[ApprovalRequest, ...],
    reason: Reason,
    cleared: list[Cleared],
) -> Verdict | None:
    for request in doomed:
        match _probe(gate, request):
            case Probe.APPROVED | Probe.CANCELLED:
                return Verdict(Outcome.DEFERRED, Reason.OWNER_DECIDED, cleared=tuple(cleared), blocked=(request,))
            case Probe.UNVERIFIABLE:
                return Verdict(Outcome.DEFERRED, Reason.UNVERIFIABLE, cleared=tuple(cleared), blocked=(request,))
            case Probe.BINDING_MISMATCH:
                return Verdict(Outcome.REFUSED, Reason.BINDING_MISMATCH, cleared=tuple(cleared), blocked=(request,))
            case Probe.MISSING:
                gate.drop(request)
                cleared.append(Cleared(request, Reason.MESSAGE_MISSING))
            case Probe.BOUND_PENDING:
                try:
                    gate.delete(request)
                except (ApprovalSurfaceError, OSError):
                    return Verdict(Outcome.REFUSED, Reason.SUPERSEDE_FAILED, cleared=tuple(cleared), blocked=(request,))
                gate.drop(request)
                cleared.append(Cleared(request, reason))
            case unreachable:
                assert_never(unreachable)
    return None


def request_owner_approval(
    intent: ApprovalIntent,
    gate: ApprovalGate,
    lease: ApprovalLease,
    journal: PostingJournal,
) -> Verdict:
    with lease.hold(intent.key) as owned:
        if not owned:
            return Verdict(Outcome.DEFERRED, Reason.LEASE_HELD)
        if journal.outstanding(intent.key) is not None:
            return Verdict(Outcome.REFUSED, Reason.POSTING_JOURNAL_STALE)
        try:
            outstanding = gate.outstanding(intent.key)
        except ApprovalRecordsError:
            return Verdict(Outcome.REFUSED, Reason.STORE_UNREADABLE)
        snapshot = tuple((request, _probe(gate, request)) for request in outstanding)
        decided = tuple(request for request, probe in snapshot if probe in (Probe.APPROVED, Probe.CANCELLED))
        if decided:
            return Verdict(Outcome.DEFERRED, Reason.OWNER_DECIDED, blocked=decided)
        unverifiable = tuple(request for request, probe in snapshot if probe is Probe.UNVERIFIABLE)
        if unverifiable:
            return Verdict(Outcome.DEFERRED, Reason.UNVERIFIABLE, blocked=unverifiable)
        mismatched = tuple(request for request, probe in snapshot if probe is Probe.BINDING_MISMATCH)
        if mismatched:
            return Verdict(Outcome.REFUSED, Reason.BINDING_MISMATCH, blocked=mismatched)
        same = tuple(sorted(
            (request for request, probe in snapshot if probe is Probe.BOUND_PENDING and request.action_hash == intent.action_hash),
            key=lambda request: (request.created_at, request.message_id),
        ))
        others = tuple(request for request, probe in snapshot if probe is Probe.BOUND_PENDING and request.action_hash != intent.action_hash)
        gone = tuple(request for request, probe in snapshot if probe is Probe.MISSING)
        cleared: list[Cleared] = []
        for request in gone:
            gate.drop(request)
            cleared.append(Cleared(request, Reason.MESSAGE_MISSING))
        if same:
            canonical, duplicates = same[0], same[1:]
            halted = _destroy(gate, duplicates, Reason.DUPLICATE_COLLAPSED, cleared)
            if halted is not None:
                return halted
            pending_reason = Reason.DUPLICATE_COLLAPSED if duplicates else None
            return Verdict(Outcome.PENDING, pending_reason, live=canonical, cleared=tuple(cleared))
        halted = _destroy(gate, others, Reason.CONTENT_CHANGED, cleared)
        if halted is not None:
            return halted
        created_at = _now()
        journal.reserve(intent.key, intent.action_hash, created_at)
        posted = gate.post(intent)
        gate.commit(intent, posted, created_at)
        journal.clear(intent.key)
        return Verdict(Outcome.POSTED, posted=posted, cleared=tuple(cleared))


class DecisionWatcher(Protocol):
    def probe(self, request: ApprovalRequest) -> Probe: ...

    def apply(self, request: ApprovalRequest, decision: Probe) -> None: ...

    def drop(self, request: ApprovalRequest) -> None: ...


class WatchOutcome(StrEnum):
    CONSUMED = "consumed"
    WAITING = "waiting"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class WatchVerdict:
    outcome: WatchOutcome
    reason: Reason | None = None


def resolve_owner_decision(
    request: ApprovalRequest,
    watcher: DecisionWatcher,
    lease: ApprovalLease,
) -> WatchVerdict:
    with lease.hold(request.key) as owned:
        if not owned:
            return WatchVerdict(WatchOutcome.SKIPPED, Reason.LEASE_HELD)
        decision = watcher.probe(request)
        match decision:
            case Probe.APPROVED | Probe.CANCELLED:
                watcher.apply(request, decision)
                watcher.drop(request)
                return WatchVerdict(WatchOutcome.CONSUMED)
            case Probe.BOUND_PENDING | Probe.MISSING | Probe.BINDING_MISMATCH | Probe.UNVERIFIABLE:
                return WatchVerdict(WatchOutcome.WAITING)
            case unreachable:
                assert_never(unreachable)
