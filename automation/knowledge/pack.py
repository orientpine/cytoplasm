"""Immutable Knowledge Adoption Contract v1 value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

Purpose: TypeAlias = Literal["cite", "synthesize", "entity", "judgment"]
Verdict: TypeAlias = Literal["hit", "no_evidence", "unavailable"]
DateBasis: TypeAlias = Literal["created", "updated", "day", "path", "none"]
Store: TypeAlias = Literal["obsidian", "wiki", "rag"]


@dataclass(frozen=True, slots=True)
class KnowledgeQuery:
    text: str
    purpose: Purpose = "cite"
    sources: frozenset[str] = frozenset({"rag", "wiki", "twin"})
    tags: frozenset[str] = frozenset()
    limit: int = 8
    caller: str = ""

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("text must not be empty")
        if self.purpose not in {"cite", "synthesize", "entity", "judgment"}:
            raise ValueError(f"unsupported purpose: {self.purpose}")
        if not self.sources.issubset({"rag", "wiki", "twin"}):
            raise ValueError("sources may only contain rag, wiki, and twin")
        if not 1 <= self.limit <= 20:
            raise ValueError("limit must be between 1 and 20")


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    id: str
    store: Store
    source_type: str
    ref: str
    title: str
    doc_date: str | None
    date_basis: DateBasis
    score: float | None
    grounded: bool | None
    authority: str | None
    expired: bool | None
    sensitivity: str | None
    content: str
    sha256: str


@dataclass(frozen=True, slots=True)
class EvidencePack:
    version: Literal["knowledge-v1"]
    query: KnowledgeQuery
    verdict: Verdict
    items: tuple[EvidenceItem, ...]
    layers: dict[str, str]
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CitationReport:
    text: str
    cited_ids: tuple[str, ...]
    stripped_ids: tuple[str, ...]
    has_citations: bool
