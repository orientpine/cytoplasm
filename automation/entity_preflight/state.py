"""State transitions for a preflighted and API-verified external write."""

from __future__ import annotations

from dataclasses import replace

from .contracts import (
    DecisionKind,
    ExternalWriteState,
    PreflightDecision,
    VerificationOutcome,
    VerificationRecord,
    WritePhase,
)

_ALLOWED: dict[WritePhase, frozenset[WritePhase]] = {
    WritePhase.PREFLIGHTED: frozenset({WritePhase.AWAITING_CLARIFY, WritePhase.READY_TO_WRITE}),
    WritePhase.AWAITING_CLARIFY: frozenset({WritePhase.READY_TO_WRITE, WritePhase.CANCELLED}),
    WritePhase.READY_TO_WRITE: frozenset({WritePhase.WRITE_IN_PROGRESS}),
    WritePhase.WRITE_IN_PROGRESS: frozenset(
        {WritePhase.WRITE_SUCCEEDED, WritePhase.WRITE_FAILED}
    ),
    WritePhase.WRITE_SUCCEEDED: frozenset({WritePhase.VERIFYING}),
    WritePhase.VERIFYING: frozenset(
        {WritePhase.VERIFIED, WritePhase.VERIFICATION_FAILED}
    ),
    WritePhase.WRITE_FAILED: frozenset(),
    WritePhase.VERIFIED: frozenset(),
    WritePhase.VERIFICATION_FAILED: frozenset(),
    WritePhase.CANCELLED: frozenset(),
}


def initial_state(decision: PreflightDecision) -> ExternalWriteState:
    if decision.decision == DecisionKind.NOT_DETECTED:
        phase = WritePhase.READY_TO_WRITE
    elif decision.needs_confirmation:
        phase = WritePhase.AWAITING_CLARIFY
    else:
        phase = WritePhase.READY_TO_WRITE
    return ExternalWriteState(
        correlation_id=decision.audit.correlation_id,
        phase=phase,
        preflight_decision=decision.decision,
    )


def transition(
    state: ExternalWriteState,
    phase: WritePhase,
    *,
    resource_id: str | None = None,
    failure_code: str | None = None,
    verification: VerificationRecord | None = None,
) -> ExternalWriteState:
    if phase not in _ALLOWED[state.phase]:
        raise ValueError(f"invalid external-write transition: {state.phase.value} -> {phase.value}")
    if phase == WritePhase.WRITE_SUCCEEDED and not resource_id:
        raise ValueError("successful write must record its resource id")
    if phase in {WritePhase.WRITE_FAILED, WritePhase.VERIFICATION_FAILED} and not failure_code:
        raise ValueError("failed state must record a safe failure code")
    if phase in {WritePhase.VERIFIED, WritePhase.VERIFICATION_FAILED}:
        if verification is None:
            raise ValueError("verification terminal state requires an API requery record")
        expected_phase = (
            WritePhase.VERIFIED
            if verification.outcome == VerificationOutcome.MATCH
            else WritePhase.VERIFICATION_FAILED
        )
        if phase != expected_phase:
            raise ValueError("verification outcome and terminal phase disagree")
    return replace(
        state,
        phase=phase,
        resource_id=resource_id if resource_id is not None else state.resource_id,
        failure_code=failure_code,
        verification=verification,
    )
