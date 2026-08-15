"""One fail-closed pre-write guard for personal-entity external writes."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import assert_never

from .audit import (
    JsonlOperationalLog,
    PrivateJsonlAuditStore,
    input_sha256,
    operational_event,
)
from .clarify import render_clarify
from .contracts import (
    AuditMetadata,
    CandidateResolver,
    DecisionKind,
    DecisionReason,
    ExternalWriteAdapter,
    ExternalWriteState,
    JsonValue,
    OperationalLogger,
    PreflightDecision,
    PreflightInput,
    SensitiveAuditStore,
    VerificationOutcome,
    VerificationRecord,
    WritePhase,
    WriteReceipt,
)
from .gate_audit import (
    GateAuditInput,
    append_decision,
    append_failure,
    append_verification,
    fingerprint,
    sensitive_audit_ref,
)
from .gate_quality import (
    RESOLVER_UNAVAILABLE,
    QualityObservation,
    emit_quality,
    error_quality_record,
    quality_record,
)
from .normalize import normalized_payload
from .policy import PolicyError, load_policy
from .resolver import DataSourceFailure, PersonalEntityResolver
from .state import initial_state, transition


@dataclass(frozen=True, slots=True)
class GuardRequest:
    """All private inputs that one guarded external write needs."""

    request: PreflightInput
    payload: Mapping[str, JsonValue]
    sources: tuple[CandidateResolver, ...]
    idempotency_key: str
    actor: str
    purpose: str
    requested_at: str


@dataclass(frozen=True, slots=True)
class GuardedWrite:
    """A verified external write, including its normalized private payload."""

    decision: PreflightDecision
    payload: Mapping[str, JsonValue]
    receipt: WriteReceipt
    verification: VerificationRecord
    state: ExternalWriteState
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _ClarificationRecorded:
    """Idempotency marker for a clarification already delivered to the owner."""


@dataclass(frozen=True, slots=True)
class _FailureRecorded:
    code: str


@dataclass(frozen=True, slots=True)
class _InProgress:
    """Fail closed while another identical request owns the external-write slot."""


_StoredResult = GuardedWrite | _ClarificationRecorded | _FailureRecorded | _InProgress


class IdempotencyStore:
    """Small boundary for one request key's exact-once state."""

    def claim(self, key: str) -> _StoredResult | None:
        raise NotImplementedError

    def store(self, key: str, result: _StoredResult) -> None:
        raise NotImplementedError


class InMemoryIdempotencyStore(IdempotencyStore):
    """Process-local exact-once store; callers retain it for retry scope."""

    def __init__(self) -> None:
        self._records: dict[str, _StoredResult] = {}

    def claim(self, key: str) -> _StoredResult | None:
        previous = self._records.get(key)
        if previous is not None:
            return previous
        self._records[key] = _InProgress()
        return None

    def store(self, key: str, result: _StoredResult) -> None:
        self._records[key] = result


@dataclass(frozen=True, slots=True)
class GateDependencies:
    """I/O capabilities kept separate from the pure request and write adapter."""

    audit_store: SensitiveAuditStore
    operational_logger: OperationalLogger | None
    idempotency_store: IdempotencyStore
    monotonic_ns: Callable[[], int] = time.monotonic_ns


@dataclass(frozen=True, slots=True)
class EntityClarificationRequired(RuntimeError):
    """The caller must print the owner-facing clarify text and exit non-zero."""

    rendered: str
    should_render: bool

    def __str__(self) -> str:
        return self.rendered


@dataclass(frozen=True, slots=True)
class EntityPreflightUnavailable(RuntimeError):
    """A resolver or idempotency failure stopped the write before the connector."""

    code: str

    def __str__(self) -> str:
        return f"ENTITY-PREFLIGHT-FAIL code={self.code} — 외부 쓰기를 시작하지 않았습니다."


@dataclass(frozen=True, slots=True)
class PostWriteVerificationFailed(RuntimeError):
    """The stored API value or identifier did not prove the successful write."""

    outcome: VerificationOutcome

    def __str__(self) -> str:
        return f"ENTITY-VERIFY-FAIL outcome={self.outcome.value}"


def production_dependencies() -> GateDependencies:
    """Build the default private-audit dependency bundle for CLI call sites."""

    return GateDependencies(
        PrivateJsonlAuditStore(), JsonlOperationalLog(), InMemoryIdempotencyStore()
    )


def guarded_write(
    guard_request: GuardRequest,
    adapter: ExternalWriteAdapter,
    dependencies: GateDependencies,
) -> GuardedWrite:
    """Resolve, audit, normalize, write once, and prove the API-stored result."""

    previous = dependencies.idempotency_store.claim(guard_request.idempotency_key)
    if previous is not None:
        return _replay(previous)
    started_ns = dependencies.monotonic_ns()
    audit_input = _audit_input(guard_request)
    try:
        decision = _resolve(guard_request)
    except (DataSourceFailure, PolicyError, TimeoutError, ValueError):
        _record_failure(audit_input, dependencies, started_ns)
        dependencies.idempotency_store.store(guard_request.idempotency_key, _FailureRecorded(RESOLVER_UNAVAILABLE))
        raise EntityPreflightUnavailable(RESOLVER_UNAVAILABLE) from None
    payload = normalized_payload(guard_request.payload, decision)
    append_decision(audit_input, decision, payload, dependencies.audit_store)
    _emit_operational_event(decision, dependencies.operational_logger)
    if decision.needs_confirmation:
        blocked = QualityObservation(_elapsed_ms(dependencies, started_ns), external_write_blocked=True)
        emit_quality(quality_record(decision, blocked), dependencies.operational_logger)
        dependencies.idempotency_store.store(guard_request.idempotency_key, _ClarificationRecorded())
        raise EntityClarificationRequired(render_clarify(decision), True)
    state = transition(initial_state(decision), WritePhase.WRITE_IN_PROGRESS)
    receipt = adapter.write(payload)
    state = transition(state, WritePhase.WRITE_SUCCEEDED, resource_id=receipt.resource_id)
    state = transition(state, WritePhase.VERIFYING)
    verification = adapter.requery(receipt, fingerprint(payload))
    written = QualityObservation(
        _elapsed_ms(dependencies, started_ns),
        external_write_blocked=False,
        verification=verification.outcome,
    )
    if verification.outcome != VerificationOutcome.MATCH:
        state = transition(
            state,
            WritePhase.VERIFICATION_FAILED,
            failure_code=f"verification_{verification.outcome.value}",
            verification=verification,
        )
        append_verification(audit_input, state, dependencies.audit_store)
        emit_quality(quality_record(decision, written), dependencies.operational_logger)
        dependencies.idempotency_store.store(guard_request.idempotency_key, _FailureRecorded("verification"))
        raise PostWriteVerificationFailed(verification.outcome)
    state = transition(state, WritePhase.VERIFIED, verification=verification)
    result = GuardedWrite(decision, payload, receipt, verification, state)
    append_verification(audit_input, state, dependencies.audit_store)
    emit_quality(quality_record(decision, written), dependencies.operational_logger)
    dependencies.idempotency_store.store(guard_request.idempotency_key, result)
    return result


def _replay(previous: _StoredResult) -> GuardedWrite:
    match previous:
        case GuardedWrite() as result:
            return replace(result, replayed=True)
        case _ClarificationRecorded():
            raise EntityClarificationRequired("ENTITY-CLARIFY 이미 확인을 요청했습니다.", False)
        case _FailureRecorded(code=code):
            raise EntityPreflightUnavailable(code)
        case _InProgress():
            raise EntityPreflightUnavailable("request_in_progress")
        case unreachable:
            assert_never(unreachable)


def _resolve(guard_request: GuardRequest) -> PreflightDecision:
    """Decide once without side effects; the caller owns failure recording."""

    if not guard_request.request.entities:
        return PreflightDecision(
            request=guard_request.request,
            candidates=(),
            selected=(),
            decision=DecisionKind.NOT_DETECTED,
            reason=DecisionReason.NO_ENTITY,
            needs_confirmation=False,
            audit=_audit_metadata(guard_request, "not_applicable"),
        )
    policy = load_policy()
    audit = _audit_metadata(guard_request, policy.version)
    return PersonalEntityResolver(guard_request.sources, policy).resolve(guard_request.request, audit)


def _audit_metadata(guard_request: GuardRequest, policy_version: str) -> AuditMetadata:
    return AuditMetadata(
        correlation_id=guard_request.idempotency_key,
        policy_version=policy_version,
        requested_at=guard_request.requested_at,
        actor=guard_request.actor,
        purpose=guard_request.purpose,
        input_sha256=input_sha256(guard_request.request.raw_text),
        sensitive_audit_ref=sensitive_audit_ref(guard_request.idempotency_key),
    )


def _elapsed_ms(dependencies: GateDependencies, started_ns: int) -> int:
    return (dependencies.monotonic_ns() - started_ns) // 1_000_000


def _audit_input(guard_request: GuardRequest) -> GateAuditInput:
    return GateAuditInput(
        request=guard_request.request,
        payload=guard_request.payload,
        correlation_id=guard_request.idempotency_key,
        timestamp=guard_request.requested_at,
    )


def _emit_operational_event(decision: PreflightDecision, logger: OperationalLogger | None) -> None:
    if logger is not None:
        logger.emit(operational_event(decision))


def _record_failure(
    audit_input: GateAuditInput,
    dependencies: GateDependencies,
    started_ns: int,
) -> None:
    append_failure(audit_input, dependencies.audit_store)
    blocked = QualityObservation(_elapsed_ms(dependencies, started_ns), external_write_blocked=True)
    emit_quality(error_quality_record(audit_input, blocked), dependencies.operational_logger)
