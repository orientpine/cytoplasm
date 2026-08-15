"""Decision policy for personal-entity preflight."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .contracts import (
    AuditMetadata,
    DecisionKind,
    DecisionReason,
    PreflightDecision,
    PreflightInput,
    ResolutionCandidate,
    SelectedValue,
    SourceKind,
)


POLICY_SEED_PATH = Path(__file__).resolve().parents[2] / "configs" / "entity-preflight.json"
"""The single tracked location of every preflight threshold and source weight.

It is an immutable seed: never written at runtime, and no other file may carry
a competing copy of these numbers.
"""


class PolicyError(ValueError):
    """The immutable policy seed is absent or unsafe."""


@dataclass(frozen=True, slots=True)
class PreflightPolicy:
    version: str
    auto_select_min_confidence: float
    conflict_min_confidence: float
    ambiguity_margin: float
    source_weights: dict[SourceKind, float]

    def __post_init__(self) -> None:
        values = (
            self.auto_select_min_confidence,
            self.conflict_min_confidence,
            self.ambiguity_margin,
            *self.source_weights.values(),
        )
        if not self.version or any(not 0.0 <= value <= 1.0 for value in values):
            raise PolicyError("policy values must be non-empty and between 0 and 1")
        if self.conflict_min_confidence > self.auto_select_min_confidence:
            raise PolicyError("conflict floor cannot exceed auto-select floor")


def load_policy(path: str | Path = POLICY_SEED_PATH) -> PreflightPolicy:
    """Load the tracked seed. Invalid/missing settings fail closed."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        thresholds = payload["thresholds"]
        raw_weights = payload["source_weights"]
        weights = {SourceKind(key): float(value) for key, value in raw_weights.items()}
        missing = set(SourceKind) - set(weights)
        if missing:
            raise PolicyError(f"source weights missing: {sorted(item.value for item in missing)}")
        return PreflightPolicy(
            version=str(payload["version"]),
            auto_select_min_confidence=float(thresholds["auto_select_min_confidence"]),
            conflict_min_confidence=float(thresholds["conflict_min_confidence"]),
            ambiguity_margin=float(thresholds["ambiguity_margin"]),
            source_weights=weights,
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
        if isinstance(error, PolicyError):
            raise
        raise PolicyError("entity-preflight policy is unreadable or invalid") from error


def _effective_confidence(candidate: ResolutionCandidate, policy: PreflightPolicy) -> float:
    return candidate.confidence * policy.source_weights[candidate.source]


def _rank_groups(
    candidates: tuple[ResolutionCandidate, ...], policy: PreflightPolicy
) -> list[tuple[float, str, tuple[ResolutionCandidate, ...]]]:
    grouped: dict[str, list[ResolutionCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.normalized_value, []).append(candidate)
    ranked = [
        (
            max(_effective_confidence(candidate, policy) for candidate in group),
            normalized,
            tuple(group),
        )
        for normalized, group in grouped.items()
    ]
    return sorted(ranked, key=lambda item: (-item[0], item[1]))


def decide(
    request: PreflightInput,
    candidates: tuple[ResolutionCandidate, ...],
    audit: AuditMetadata,
    policy: PreflightPolicy,
) -> PreflightDecision:
    """Apply deterministic all-mentions-must-resolve preflight rules.

    A different normalized value is a conflict when it is independently
    credible or too close to the top score. Noise below both checks does not
    prevent a high-confidence automatic choice.
    """
    if audit.policy_version != policy.version:
        raise PolicyError("audit metadata and loaded policy versions differ")
    mention_ids = {entity.mention_id for entity in request.entities}
    candidate_ids = {candidate.candidate_id for candidate in candidates}
    if len(candidate_ids) != len(candidates):
        raise ValueError("candidate ids must be unique")
    query_mentions = {
        query.query_id: query.subject_mention_id for query in request.relationship_queries
    }
    if any(candidate.mention_id not in mention_ids for candidate in candidates):
        raise ValueError("candidate references an unknown mention")
    if any(
        candidate.relationship_query_id is not None
        and query_mentions.get(candidate.relationship_query_id) != candidate.mention_id
        for candidate in candidates
    ):
        raise ValueError("candidate references an unknown or unrelated relationship query")
    if not request.entities:
        return PreflightDecision(
            request=request,
            candidates=candidates,
            selected=(),
            decision=DecisionKind.NOT_DETECTED,
            reason=DecisionReason.NO_ENTITY,
            needs_confirmation=False,
            audit=audit,
        )

    selected: list[SelectedValue] = []
    reasons: list[DecisionReason] = []
    for entity in request.entities:
        mention_candidates = tuple(
            candidate for candidate in candidates if candidate.mention_id == entity.mention_id
        )
        ranked = _rank_groups(mention_candidates, policy)
        if not ranked:
            reasons.append(DecisionReason.NO_CANDIDATE)
            continue
        top_score, normalized, supporting = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else -math.inf
        conflicting = len(ranked) > 1 and (
            second_score >= policy.conflict_min_confidence
            or top_score - second_score < policy.ambiguity_margin
        )
        if conflicting:
            reasons.append(DecisionReason.CANDIDATE_CONFLICT)
            continue
        if top_score < policy.auto_select_min_confidence:
            reasons.append(DecisionReason.LOW_CONFIDENCE)
            continue
        best_candidate = max(
            supporting, key=lambda candidate: _effective_confidence(candidate, policy)
        )
        selected.append(
            SelectedValue(
                mention_id=entity.mention_id,
                normalized_value=normalized,
                display_value=best_candidate.display_value,
                confidence=round(top_score, 6),
                supporting_candidate_ids=tuple(item.candidate_id for item in supporting),
            )
        )

    if reasons:
        reason = (
            DecisionReason.CANDIDATE_CONFLICT
            if DecisionReason.CANDIDATE_CONFLICT in reasons
            else DecisionReason.LOW_CONFIDENCE
            if DecisionReason.LOW_CONFIDENCE in reasons
            else DecisionReason.NO_CANDIDATE
        )
        return PreflightDecision(
            request=request,
            candidates=candidates,
            selected=tuple(selected),
            decision=DecisionKind.CONFIRMATION_REQUIRED,
            reason=reason,
            needs_confirmation=True,
            audit=audit,
        )
    return PreflightDecision(
        request=request,
        candidates=candidates,
        selected=tuple(selected),
        decision=DecisionKind.AUTO_SELECTED,
        reason=DecisionReason.SINGLE_HIGH_CONFIDENCE,
        needs_confirmation=False,
        audit=audit,
    )
