from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .frozen_model import FrozenModel

_MAX_KEYWORDS = 5


class SensitivityFlag(str, Enum):
    PII = "PII"
    IP = "IP"
    NDA = "NDA"
    NONE = "NONE"


class CitationStatus(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    REDACT = "REDACT"


@dataclass(frozen=True)
class DocRecord(FrozenModel):
    doc_id: str
    source_type: str
    participants: list[str]
    author: str | None
    datetime: datetime | None
    raw_text: str
    lang: str
    confidence: float
    sensitivity_flag: SensitivityFlag
    source_url: str | None = None


@dataclass(frozen=True)
class Provenance(FrozenModel):
    source_id: str
    verbatim: str
    location: str
    speaker: str | None
    sender: str | None
    datetime: datetime | None
    source_url: str | None = None


@dataclass(frozen=True)
class EvidenceUnit(FrozenModel):
    unit_id: str
    fact: str
    provenances: list[Provenance]
    bucket: str
    sensitivity_flag: SensitivityFlag
    conflict: bool = False


@dataclass(frozen=True)
class KPI(FrozenModel):
    name: str
    unit: str
    baseline: str
    target: str
    weight: int
    method: str
    env: str
    rationale: str


@dataclass(frozen=True)
class IR(FrozenModel):
    title: str
    trl_start: int
    trl_end: int
    objectives: list[str]
    keywords: list[str]
    kpis: list[KPI]

    def __post_init__(self) -> None:
        if len(self.keywords) > _MAX_KEYWORDS:
            raise ValueError(f"keywords accepts at most {_MAX_KEYWORDS} entries")
        if self.trl_start >= self.trl_end:
            raise ValueError("trl_start must be less than trl_end")

        from .validators import validate_kpi_sum

        validate_kpi_sum(self.kpis)


@dataclass(frozen=True)
class TraceLink(FrozenModel):
    ir_slot: str
    evidence_unit_ids: list[str]


@dataclass(frozen=True)
class TraceabilityMatrix(FrozenModel):
    links: list[TraceLink]


@dataclass(frozen=True)
class WorkPackage(FrozenModel):
    wp_id: str
    title: str
    lead: str
    months: int
    deliverables: list[str]
    start_month: int | None = None
    end_month: int | None = None

    def __post_init__(self) -> None:
        if self.start_month is None and self.end_month is None:
            return
        if self.start_month is None or self.end_month is None:
            message = "WorkPackage requires both start_month and end_month"
            raise ValueError(message)
        if not 1 <= self.start_month <= self.end_month:
            message = "WorkPackage requires 1 <= start_month <= end_month"
            raise ValueError(message)
        if self.months != self.end_month - self.start_month + 1:
            message = "WorkPackage months must equal its inclusive month span"
            raise ValueError(message)


@dataclass(frozen=True)
class PlanSpec(IR):
    work_packages: list[WorkPackage]
    page_budget: dict[str, int]
    traceability: TraceabilityMatrix

    @property
    def total_months(self) -> int:
        """Project horizon; explicit spans overlap, legacy packages follow in order."""
        elapsed = 0
        for package in sorted(self.work_packages, key=lambda item: item.wp_id):
            elapsed = (
                max(elapsed, package.end_month)
                if package.end_month is not None else elapsed + package.months
            )
        return elapsed


@dataclass(frozen=True)
class Claim(FrozenModel):
    text: str
    source_ids: list[str]


@dataclass(frozen=True)
class SectionDraft(FrozenModel):
    section_id: str
    title: str
    body: str
    claims: list[Claim]


@dataclass(frozen=True)
class Rejection(FrozenModel):
    criterion: str
    cell: str
    reason: str


@dataclass(frozen=True)
class CriticReport(FrozenModel):
    rubric_scores: dict[str, float]
    rejections: list[Rejection]
    overall_score: float


@dataclass(frozen=True)
class RenderPatch(FrozenModel):
    slot: str
    text: str
    table_rows: list[list[str]] | None = None


@dataclass(frozen=True)
class NodeRef(FrozenModel):
    element_path: str
    index: int
    ancestor_table_id: str | None = None


@dataclass(frozen=True)
class AnchorMap(FrozenModel):
    slots: dict[str, NodeRef]


@dataclass(frozen=True)
class NodeLog(FrozenModel):
    node_name: str
    inputs_hash: str
    outputs_hash: str


@dataclass(frozen=True)
class RunResult(FrozenModel):
    artifact_path: str
    citations_path: str
    node_log: list[NodeLog]
    planspec_path: str = ""
    drafts_path: str = ""
    pms_path: str = ""


@dataclass(frozen=True)
class EvidenceNode(FrozenModel):
    """A PUBLIC evidence unit node in the knowledge graph."""

    source_id: str
    sensitivity: str = "PUBLIC"  # always PUBLIC in graph


@dataclass(frozen=True)
class ClaimNode(FrozenModel):
    """A claim node tied to a proposal section."""

    claim_id: str
    section_id: str
    text: str


@dataclass(frozen=True)
class EdgeClaimToEvidence(FrozenModel):
    """A directed edge from a claim to a supporting evidence source."""

    claim_id: str
    source_id: str
    relation: str = "supports"


@dataclass(frozen=True)
class EvidenceGraph(FrozenModel):
    """Immutable knowledge graph: claims -> evidence -> sources.

    All collections are sorted tuples for determinism.
    """

    evidence_nodes: tuple[EvidenceNode, ...] = ()
    claim_nodes: tuple[ClaimNode, ...] = ()
    edges: tuple[EdgeClaimToEvidence, ...] = ()

    @property
    def coverage_score(self) -> float:
        """Fraction of claims that have at least one evidence edge."""
        if not self.claim_nodes:
            return 0.0
        connected = {e.claim_id for e in self.edges}
        return len(connected & {c.claim_id for c in self.claim_nodes}) / len(self.claim_nodes)
