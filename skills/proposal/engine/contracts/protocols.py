from __future__ import annotations

from typing import Protocol

from .collab_models import AdviseRanking, EvidenceCandidate, ReviewerOpinion
from .models import EvidenceUnit, PlanSpec, SectionDraft


class PMSQuery(Protocol):
    def evidence_for_bucket(self, bucket: str) -> list[EvidenceUnit]: ...

    def public_evidence(self) -> list[EvidenceUnit]: ...

    def resolve(self, source_id: str) -> EvidenceUnit | None: ...


class LLMClient(Protocol):
    def complete(self, role: str, prompt: str) -> str: ...


class ResearchProvider(Protocol):
    """OMO ultraresearch/ultimate-browsing 산출 SYNTHESIS.md를 소비해 후보를 반환."""

    def candidates(self, synthesis_path: str) -> list[EvidenceCandidate]: ...


class Reviewer(Protocol):
    """리뷰어: drafts+planspec+pms를 보고 구조화된 의견 목록을 반환."""

    def review(
        self,
        drafts: list[SectionDraft],
        planspec: PlanSpec,
        pms: PMSQuery,
    ) -> list[ReviewerOpinion]: ...


class AdviseScorer(Protocol):
    """후보를 4기준(목표적합·신뢰도·최신성·PUBLIC안전)으로 채점·정렬."""

    def score(self, candidates: list[EvidenceCandidate]) -> AdviseRanking: ...
