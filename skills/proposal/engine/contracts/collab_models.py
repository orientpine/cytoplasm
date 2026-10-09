from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import FrozenModel


@dataclass(frozen=True)
class EvidenceCandidate(FrozenModel):
    candidate_id: str
    source_url: str
    verbatim: str
    bucket: str
    origin: Literal["external_live", "corpus"]
    advise_score: float | None = None

    @classmethod
    def from_live(cls, source_url: str, verbatim: str, bucket: str) -> EvidenceCandidate:
        from .ids import stable_id

        return cls(
            candidate_id=stable_id(source_url, verbatim[:200]),
            source_url=source_url,
            verbatim=verbatim,
            bucket=bucket,
            origin="external_live",
        )


@dataclass(frozen=True)
class ReviewerOpinion(FrozenModel):
    reviewer_id: str
    locator: str
    opinion_text: str
    proposed_edit: str | None = None
    source_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class AdviseRanking(FrozenModel):
    ranked: tuple[str, ...]
    rationale: dict[str, str]


@dataclass(frozen=True)
class MergeVariant(FrozenModel):
    reviewer_id: str
    text: str
    source_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class MergeClaim(FrozenModel):
    text: str
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.source_ids:
            raise ValueError("MergeClaim.source_ids must not be empty")


@dataclass(frozen=True)
class MergeHunk(FrozenModel):
    hunk_id: str
    locator: str
    base_text: str
    base_claims: tuple[MergeClaim, ...]
    variants: tuple[MergeVariant, ...]
    conflict: bool = False

    def __post_init__(self) -> None:
        if self.conflict and len(self.variants) < 2:
            raise ValueError("conflict=True requires at least 2 variants")


@dataclass(frozen=True)
class MergePlan(FrozenModel):
    hunks: tuple[MergeHunk, ...]


@dataclass(frozen=True)
class MergeDecision(FrozenModel):
    hunk_id: str
    chosen: str
    final_text: str
    final_claims: tuple[MergeClaim, ...]


@dataclass(frozen=True)
class SectionOpinionEntry(FrozenModel):
    reviewer_id: str
    opinion_text: str
    chosen: bool = False


@dataclass(frozen=True)
class TranscriptSection(FrozenModel):
    locator: str
    opinions: tuple[SectionOpinionEntry, ...]
    aggregated: str
    result_text: str
    unresolved: bool = False


@dataclass(frozen=True)
class RevisionTranscript(FrozenModel):
    sections: tuple[TranscriptSection, ...]
    plan_name: str = ""
    timestamp: str = ""


__all__ = [
    "AdviseRanking",
    "EvidenceCandidate",
    "MergeClaim",
    "MergeDecision",
    "MergeHunk",
    "MergePlan",
    "MergeVariant",
    "ReviewerOpinion",
    "RevisionTranscript",
    "SectionOpinionEntry",
    "TranscriptSection",
]
