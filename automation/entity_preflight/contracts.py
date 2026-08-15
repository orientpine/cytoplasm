"""Typed contract for resolving personal proper nouns before an external write.

The contract deliberately keeps raw input and normalized personal values in
memory-only objects. Callers must persist those objects only through a
``SensitiveAuditStore``; normal operational logs consume the redacted view from
``audit.operational_event``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeAlias

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class EntityKind(str, Enum):
    PERSON = "person"
    ORGANIZATION = "organization"
    PROJECT = "project"
    PLACE = "place"
    ACCOUNT = "account"
    OTHER = "other"


class SourceKind(str, Enum):
    EXPLICIT_INPUT = "explicit_input"
    RELATIONSHIP_QUERY = "relationship_query"
    ADDRESSBOOK_ORGANIZATION = "addressbook.organization"
    ADDRESSBOOK_CONTACTS = "addressbook.contacts"
    ADDRESSBOOK_HISTORY = "addressbook.history"
    PERSONAL_RAG = "personal_rag"
    HERMES_MEMORY = "hermes_memory"


class DecisionKind(str, Enum):
    """Outcome of the preflight.

    ``CONFIRMATION_REQUIRED`` is settled by an in-conversation ``ENTITY-CLARIFY``
    turn (see ``clarify.render_clarify``), never by an owner-approval record.
    """

    AUTO_SELECTED = "auto_selected"
    CONFIRMATION_REQUIRED = "confirmation_required"
    NOT_DETECTED = "not_detected"


class DecisionReason(str, Enum):
    SINGLE_HIGH_CONFIDENCE = "single_high_confidence"
    CANDIDATE_CONFLICT = "candidate_conflict"
    LOW_CONFIDENCE = "low_confidence"
    NO_CANDIDATE = "no_candidate"
    NO_ENTITY = "no_entity"


class WritePhase(str, Enum):
    """Lifecycle of one preflighted external write.

    ``AWAITING_CLARIFY`` waits for the owner's next conversation turn, not for a
    reaction on an approval message.
    """

    PREFLIGHTED = "preflighted"
    AWAITING_CLARIFY = "awaiting_clarify"
    READY_TO_WRITE = "ready_to_write"
    WRITE_IN_PROGRESS = "write_in_progress"
    WRITE_SUCCEEDED = "write_succeeded"
    WRITE_FAILED = "write_failed"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    CANCELLED = "cancelled"


class VerificationOutcome(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DetectedEntity:
    """One proper-noun span detected in the original user text."""

    mention_id: str
    surface: str
    entity_kind: EntityKind
    start: int
    end: int

    def __post_init__(self) -> None:
        if not self.mention_id or not self.surface or self.start < 0 or self.end <= self.start:
            raise ValueError("invalid detected entity")


@dataclass(frozen=True, slots=True)
class RelationshipQuery:
    """A personal relation lookup derived from one detected mention."""

    query_id: str
    subject_mention_id: str
    relation: str
    target_kind: EntityKind
    question: str

    def __post_init__(self) -> None:
        if not all((self.query_id, self.subject_mention_id, self.relation, self.question)):
            raise ValueError("relationship query fields must be non-empty")


@dataclass(frozen=True, slots=True)
class PreflightInput:
    request_id: str
    raw_text: str
    target_system: str
    operation: str
    entities: tuple[DetectedEntity, ...]
    relationship_queries: tuple[RelationshipQuery, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.request_id, self.raw_text, self.target_system, self.operation)):
            raise ValueError("preflight input fields must be non-empty")
        mention_ids = {entity.mention_id for entity in self.entities}
        if len(mention_ids) != len(self.entities):
            raise ValueError("mention ids must be unique")
        if any(
            entity.end > len(self.raw_text)
            or self.raw_text[entity.start : entity.end] != entity.surface
            for entity in self.entities
        ):
            raise ValueError("entity span must match the original input")
        query_ids = {query.query_id for query in self.relationship_queries}
        if len(query_ids) != len(self.relationship_queries):
            raise ValueError("relationship query ids must be unique")
        if any(query.subject_mention_id not in mention_ids for query in self.relationship_queries):
            raise ValueError("relationship query references an unknown mention")


@dataclass(frozen=True, slots=True)
class ResolutionCandidate:
    """A source-attributed candidate. Confidence is calibrated to [0, 1]."""

    candidate_id: str
    mention_id: str
    source: SourceKind
    normalized_value: str
    display_value: str
    confidence: float
    source_ref: str
    relationship_query_id: str | None = None

    def __post_init__(self) -> None:
        if not all(
            (self.candidate_id, self.mention_id, self.normalized_value, self.display_value, self.source_ref)
        ):
            raise ValueError("candidate fields must be non-empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("candidate confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class SelectedValue:
    mention_id: str
    normalized_value: str
    display_value: str
    confidence: float
    supporting_candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditMetadata:
    correlation_id: str
    policy_version: str
    requested_at: str
    actor: str
    purpose: str
    input_sha256: str
    sensitive_audit_ref: str


@dataclass(frozen=True, slots=True)
class PreflightDecision:
    """Full preflight outcome: candidates, selection, clarify need, audit trail."""

    request: PreflightInput
    candidates: tuple[ResolutionCandidate, ...]
    selected: tuple[SelectedValue, ...]
    decision: DecisionKind
    reason: DecisionReason
    needs_confirmation: bool
    audit: AuditMetadata


@dataclass(frozen=True, slots=True)
class WriteReceipt:
    external_system: str
    resource_id: str
    write_operation: str
    completed_at: str


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    """Result of an API read performed after the external write returned success."""

    external_system: str
    resource_id: str
    api_operation: str
    queried_at: str
    outcome: VerificationOutcome
    expected_fingerprint: str
    observed_fingerprint: str | None
    sensitive_evidence_ref: str


@dataclass(frozen=True, slots=True)
class ExternalWriteState:
    correlation_id: str
    phase: WritePhase
    preflight_decision: DecisionKind
    resource_id: str | None = None
    failure_code: str | None = None
    verification: VerificationRecord | None = None


class CandidateResolver(Protocol):
    """Adapter boundary for personal RAG, Hermes memory, or address-book lookup."""

    source: SourceKind

    def resolve(
        self,
        request: PreflightInput,
        query: RelationshipQuery | None,
    ) -> tuple[ResolutionCandidate, ...]: ...


class SensitiveAuditStore(Protocol):
    """Private store allowed to receive raw input and normalized personal values."""

    def append(self, event: Mapping[str, JsonValue]) -> str: ...


class OperationalLogger(Protocol):
    """General logger; callers must pass only a redacted operational event."""

    def emit(self, event: Mapping[str, JsonValue]) -> None: ...


class ExternalWriteAdapter(Protocol):
    """A gated writer whose success must be followed by an API requery."""

    def write(self, payload: Mapping[str, JsonValue]) -> WriteReceipt: ...

    def requery(self, receipt: WriteReceipt, expected_fingerprint: str) -> VerificationRecord: ...
