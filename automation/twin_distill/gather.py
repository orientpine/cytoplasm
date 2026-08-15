"""Patent-safe evidence gathering for inferred decision-twin candidates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol

_PATENT_SENSITIVITY: Final = "patent-sensitive"


@dataclass(frozen=True, slots=True)
class EvidenceMetadata:
    sensitivity: str | None = None
    source_type: str = ""


@dataclass(frozen=True, slots=True)
class RecallSearchResult:
    source: str
    content: str
    metadata: EvidenceMetadata


@dataclass(frozen=True, slots=True)
class EvidenceExcerpt:
    source_key: str
    content: str
    metadata: EvidenceMetadata


@dataclass(frozen=True, slots=True)
class GatherRequest:
    query: str
    conversation_excerpts: tuple[EvidenceExcerpt, ...] = ()
    meeting_excerpts: tuple[EvidenceExcerpt, ...] = ()


@dataclass(frozen=True, slots=True)
class DistillationContext:
    query: str
    evidence: tuple[EvidenceExcerpt, ...]


@dataclass(frozen=True, slots=True)
class NoEligibleEvidenceError(Exception):
    query: str

    def __str__(self) -> str:
        return f"no non-sensitive evidence is available for query {self.query!r}"


class RecallSearchClient(Protocol):
    def search(self, query: str) -> tuple[RecallSearchResult, ...]: ...


def gather_context(request: GatherRequest, search_client: RecallSearchClient) -> DistillationContext:
    """Exclude every patent-sensitive item before serializing any LLM prompt."""
    recalled = tuple(
        EvidenceExcerpt(result.source, result.content, result.metadata)
        for result in search_client.search(request.query)
    )
    candidates = recalled + request.conversation_excerpts + request.meeting_excerpts
    evidence = tuple(
        item for item in candidates if item.metadata.sensitivity != _PATENT_SENSITIVITY
    )
    if not evidence:
        raise NoEligibleEvidenceError(request.query)
    return DistillationContext(request.query, evidence)


def render_prompt(context: DistillationContext) -> str:
    evidence = "\n\n".join(
        f"source_key: {item.source_key}\nexcerpt:\n{item.content}" for item in context.evidence
    )
    return (
        "Distill one cautious decision-twin judgment pattern from the evidence below.\n"
        "Return only a Markdown body. It must contain `## Evidence` with at least one "
        "`source_key: ...` citation and a non-empty `## Counterexample` section.\n"
        f"Question: {context.query}\n\n"
        f"Evidence:\n{evidence}\n"
    )
