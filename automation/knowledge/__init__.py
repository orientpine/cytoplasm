"""Knowledge Adoption Contract v1 public surface."""

from __future__ import annotations

from automation.knowledge.facade import collect_evidence
from automation.knowledge.pack import CitationReport, EvidenceItem, EvidencePack, KnowledgeQuery
from automation.knowledge.render import render_citations, render_verdict, validate_citations

__all__ = [
    "CitationReport",
    "EvidenceItem",
    "EvidencePack",
    "KnowledgeQuery",
    "collect_evidence",
    "render_citations",
    "render_verdict",
    "validate_citations",
]
