"""Sensitive record construction for the common entity-preflight write guard.

These records carry raw text and normalized personal values, so they may only
ever reach a :class:`~.contracts.SensitiveAuditStore`. Every record repeats
``decision_id`` so that a misjudgement noticed through the PII-free metrics in
``gate_quality`` can be traced back to the rationale kept here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from .contracts import (
    ExternalWriteState,
    JsonValue,
    PreflightDecision,
    PreflightInput,
    SensitiveAuditStore,
)

_SENSITIVE_REF_PREFIX: Final = "private://entity-preflight/"


@dataclass(frozen=True, slots=True)
class GateAuditInput:
    """Private context shared by every audit event for one guarded write."""

    request: PreflightInput
    payload: Mapping[str, JsonValue]
    correlation_id: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class MissingVerificationError(RuntimeError):
    """A terminal write state without a readback record violates the guard contract."""

    def __str__(self) -> str:
        return "entity preflight verification record is missing"


def sensitive_audit_ref(correlation_id: str) -> str:
    """Return the one pointer format from a metric record into the private store."""

    return f"{_SENSITIVE_REF_PREFIX}{correlation_id}"


def fingerprint(payload: Mapping[str, JsonValue]) -> str:
    """Hash the exact normalized payload that the write adapter receives."""

    encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def append_decision(
    audit_input: GateAuditInput,
    decision: PreflightDecision,
    normalized_payload: Mapping[str, JsonValue],
    audit_store: SensitiveAuditStore,
) -> None:
    """Persist the full raw and normalized decision only to the private audit store."""

    candidates: list[JsonValue] = [
        {
            "candidate_id": candidate.candidate_id,
            "confidence": candidate.confidence,
            "display_value": candidate.display_value,
            "normalized_value": candidate.normalized_value,
            "rationale": candidate.source.value,
            "source_ref": candidate.source_ref,
        }
        for candidate in decision.candidates
    ]
    selected: list[JsonValue] = [
        {"mention_id": value.mention_id, "normalized_value": value.normalized_value}
        for value in decision.selected
    ]
    audit_store.append(
        {
            "event": "entity_preflight_decision",
            "decision_id": audit_input.correlation_id,
            "raw_text": audit_input.request.raw_text,
            "normalized_payload": dict(normalized_payload),
            "decision_method": decision.reason.value,
            "chosen_candidates": selected,
            "candidates": candidates,
            "timestamp": audit_input.timestamp,
            "target_system": audit_input.request.target_system,
            "task_id": audit_input.request.request_id,
        }
    )


def append_failure(audit_input: GateAuditInput, audit_store: SensitiveAuditStore) -> None:
    """Persist a resolver failure without copying raw data to operational logs."""

    audit_store.append(
        {
            "event": "entity_preflight_failure",
            "raw_text": audit_input.request.raw_text,
            "task_id": audit_input.request.request_id,
            "target_system": audit_input.request.target_system,
            "timestamp": audit_input.timestamp,
        }
    )


def append_verification(
    audit_input: GateAuditInput,
    state: ExternalWriteState,
    audit_store: SensitiveAuditStore,
) -> None:
    """Persist a successful or failed API readback in the access-controlled record."""

    verification = state.verification
    if verification is None:
        raise MissingVerificationError()
    audit_store.append(
        {
            "event": "entity_preflight_verification",
            "decision_id": audit_input.correlation_id,
            "task_id": audit_input.request.request_id,
            "target_system": audit_input.request.target_system,
            "timestamp": audit_input.timestamp,
            "phase": state.phase.value,
            "resource_id": verification.resource_id,
            "outcome": verification.outcome.value,
        }
    )
