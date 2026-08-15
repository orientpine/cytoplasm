"""PII-free quality record for one guarded personal-entity write attempt.

This is the **only** entity-preflight record allowed in general operational
logs and in metric labels. Every field is an opaque identifier, an enum value,
a count or a duration: raw text, mention surfaces, relationship expressions,
candidate and selected values, and retrieved document content are excluded by
construction and stay in the access-controlled store built by ``gate_audit``.

``decision_id`` / ``sensitive_audit_ref`` are the join back to that store, so a
misjudgement seen in the metrics can always be traced to its rationale.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final, assert_never

from .contracts import (
    DecisionKind,
    DecisionReason,
    JsonValue,
    OperationalLogger,
    PreflightDecision,
    VerificationOutcome,
)
from .gate_audit import GateAuditInput, sensitive_audit_ref

QUALITY_EVENT: Final = "entity_preflight_quality"
RESOLVER_UNAVAILABLE: Final = "resolver_unavailable"
_UNKNOWN_POLICY: Final = "unknown"


class QualityDecision(str, Enum):
    """What the preflight concluded, as published to operational metrics."""

    AUTO_NORMALIZED = "auto_normalized"
    CONFIRMATION_REQUESTED = "confirmation_requested"
    UNRESOLVED = "unresolved"
    ERROR = "error"


class ConfidenceBucket(str, Enum):
    """Coarse confidence band. The exact score stays in the private store."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


@dataclass(frozen=True, slots=True)
class QualityObservation:
    """Terminal facts about one guarded attempt that the decision cannot carry."""

    latency_ms: int
    external_write_blocked: bool
    verification: VerificationOutcome | None = None


@dataclass(frozen=True, slots=True)
class GateQualityRecord:
    """PII-free summary of one guarded attempt."""

    decision_id: str
    sensitive_audit_ref: str
    channel: str
    surface: str
    decision: QualityDecision
    reason: str
    confidence_bucket: ConfidenceBucket
    source_kinds: tuple[str, ...]
    entity_count: int
    latency_ms: int
    external_write_blocked: bool
    verification: VerificationOutcome | None
    policy_version: str

    def to_event(self) -> dict[str, JsonValue]:
        """Return the durable JSONL and operational-log form of this record."""

        return {
            "event": QUALITY_EVENT,
            "decision_id": self.decision_id,
            "sensitive_audit_ref": self.sensitive_audit_ref,
            "channel": self.channel,
            "surface": self.surface,
            "decision": self.decision.value,
            "reason": self.reason,
            "confidence_bucket": self.confidence_bucket.value,
            "source_kinds": list(self.source_kinds),
            "entity_count": self.entity_count,
            "latency_ms": self.latency_ms,
            "external_write_blocked": self.external_write_blocked,
            "verification": None if self.verification is None else self.verification.value,
            "policy_version": self.policy_version,
        }


def quality_record(
    decision: PreflightDecision,
    observation: QualityObservation,
) -> GateQualityRecord | None:
    """Summarize one decided attempt, or ``None`` when nothing had to be resolved.

    A request without a detected personal proper noun has no preflight quality
    to measure; counting it would dilute every published rate.
    """

    kind = _quality_decision(decision)
    if kind is None:
        return None
    confidences = [value.confidence for value in decision.selected]
    return GateQualityRecord(
        decision_id=decision.audit.correlation_id,
        sensitive_audit_ref=decision.audit.sensitive_audit_ref,
        channel=decision.request.target_system,
        surface=decision.request.operation,
        decision=kind,
        reason=decision.reason.value,
        confidence_bucket=_bucket(min(confidences) if confidences else None),
        source_kinds=tuple(sorted({item.source.value for item in decision.candidates})),
        entity_count=len(decision.request.entities),
        latency_ms=observation.latency_ms,
        external_write_blocked=observation.external_write_blocked,
        verification=observation.verification,
        policy_version=decision.audit.policy_version,
    )


def error_quality_record(
    audit_input: GateAuditInput,
    observation: QualityObservation,
) -> GateQualityRecord:
    """Summarize an attempt that failed before any decision could be reached."""

    return GateQualityRecord(
        decision_id=audit_input.correlation_id,
        sensitive_audit_ref=sensitive_audit_ref(audit_input.correlation_id),
        channel=audit_input.request.target_system,
        surface=audit_input.request.operation,
        decision=QualityDecision.ERROR,
        reason=RESOLVER_UNAVAILABLE,
        confidence_bucket=ConfidenceBucket.NONE,
        source_kinds=(),
        entity_count=len(audit_input.request.entities),
        latency_ms=observation.latency_ms,
        external_write_blocked=observation.external_write_blocked,
        verification=observation.verification,
        policy_version=_UNKNOWN_POLICY,
    )


def emit_quality(record: GateQualityRecord | None, logger: OperationalLogger | None) -> None:
    """Publish one PII-free record to the general operational log.

    The record deliberately does not reach the sensitive store: that store
    exists to contain personal data, and mixing metric rows into it only
    widens what a metrics reader has to be trusted with.
    """

    if record is not None and logger is not None:
        logger.emit(record.to_event())


def decode_quality_event(event: Mapping[str, JsonValue]) -> GateQualityRecord | None:
    """Parse one durable line back into a record; other audit events yield ``None``."""

    if event.get("event") != QUALITY_EVENT:
        return None
    sources = event.get("source_kinds")
    outcome = event.get("verification")
    return GateQualityRecord(
        decision_id=_string(event, "decision_id"),
        sensitive_audit_ref=_string(event, "sensitive_audit_ref"),
        channel=_string(event, "channel"),
        surface=_string(event, "surface"),
        decision=QualityDecision(_string(event, "decision")),
        reason=_string(event, "reason"),
        confidence_bucket=ConfidenceBucket(_string(event, "confidence_bucket")),
        source_kinds=tuple(str(item) for item in sources) if isinstance(sources, list) else (),
        entity_count=_integer(event, "entity_count"),
        latency_ms=_integer(event, "latency_ms"),
        external_write_blocked=event.get("external_write_blocked") is True,
        verification=None if outcome is None else VerificationOutcome(str(outcome)),
        policy_version=_string(event, "policy_version"),
    )


def _quality_decision(decision: PreflightDecision) -> QualityDecision | None:
    match decision.decision:
        case DecisionKind.AUTO_SELECTED:
            return QualityDecision.AUTO_NORMALIZED
        case DecisionKind.CONFIRMATION_REQUIRED:
            if decision.reason == DecisionReason.NO_CANDIDATE:
                return QualityDecision.UNRESOLVED
            return QualityDecision.CONFIRMATION_REQUESTED
        case DecisionKind.NOT_DETECTED:
            return None
        case unreachable:
            assert_never(unreachable)


def _bucket(confidence: float | None) -> ConfidenceBucket:
    if confidence is None:
        return ConfidenceBucket.NONE
    if confidence < 0.50:
        return ConfidenceBucket.LOW
    if confidence < 0.70:
        return ConfidenceBucket.MEDIUM
    if confidence < 0.85:
        return ConfidenceBucket.HIGH
    return ConfidenceBucket.VERY_HIGH


def _string(event: Mapping[str, JsonValue], key: str) -> str:
    value = event.get(key)
    return value if isinstance(value, str) else ""


def _integer(event: Mapping[str, JsonValue], key: str) -> int:
    value = event.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
