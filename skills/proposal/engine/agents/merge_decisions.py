from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..contracts.collab_models import (
    MergeClaim,
    MergeDecision,
    MergeHunk,
    MergePlan,
)
from ..contracts.models import FrozenModel


@dataclass(frozen=True)
class _DecisionInput(FrozenModel):
    hunk_id: str
    chosen: str
    custom_text: str | None = None
    custom_source_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class _DecisionsPayload(FrozenModel):
    decisions: tuple[_DecisionInput, ...] = field(default_factory=tuple)


def resolve_decisions(
    plan: MergePlan,
    decisions_input: list[_DecisionInput] | tuple[_DecisionInput, ...],
    *,
    public_source_ids: frozenset[str] | None = None,
) -> list[MergeDecision]:
    """Apply decisions to a merge plan."""
    decision_by_hunk = _decision_map(decisions_input, plan)
    result: list[MergeDecision] = []

    for hunk in plan.hunks:
        decision_input = decision_by_hunk.get(hunk.hunk_id)
        if decision_input is None:
            raise ValueError(
                f"No decision provided for hunk {hunk.hunk_id!r}. "
                "All hunks must have a corresponding decision."
            )
        result.append(_apply_decision(hunk, decision_input, public_source_ids))

    return result


def resolve_decisions_from_file(
    plan: MergePlan,
    decisions_path: str | Path,
    *,
    public_source_ids: frozenset[str] | None = None,
) -> list[MergeDecision]:
    """Load decisions from a JSON file and apply them to a merge plan."""
    payload = _DecisionsPayload.model_validate_json(
        Path(decisions_path).read_text(encoding="utf-8")
    )
    return resolve_decisions(plan, payload.decisions, public_source_ids=public_source_ids)


def resolve_decisions_from_stdin(
    plan: MergePlan,
    *,
    public_source_ids: frozenset[str] | None = None,
) -> list[MergeDecision]:
    """Read decisions from stdin and apply them to a merge plan."""
    payload = _DecisionsPayload.model_validate_json(sys.stdin.read())
    return resolve_decisions(plan, payload.decisions, public_source_ids=public_source_ids)


def _decision_map(
    decisions_input: list[_DecisionInput] | tuple[_DecisionInput, ...], plan: MergePlan
) -> dict[str, _DecisionInput]:
    plan_hunk_ids = {hunk.hunk_id for hunk in plan.hunks}
    decision_by_hunk: dict[str, _DecisionInput] = {}

    for decision_input in decisions_input:
        if decision_input.hunk_id not in plan_hunk_ids:
            raise ValueError(f"Decision provided for unknown hunk {decision_input.hunk_id!r}.")
        if decision_input.hunk_id in decision_by_hunk:
            raise ValueError(f"Duplicate decision provided for hunk {decision_input.hunk_id!r}.")
        decision_by_hunk[decision_input.hunk_id] = decision_input

    return decision_by_hunk


def _apply_decision(
    hunk: MergeHunk,
    decision_input: _DecisionInput,
    public_source_ids: frozenset[str] | None,
) -> MergeDecision:
    match decision_input.chosen:
        case "base":
            return MergeDecision(
                hunk_id=hunk.hunk_id,
                chosen="base",
                final_text=hunk.base_text,
                final_claims=hunk.base_claims,
            )
        case "custom":
            return _custom_decision(hunk, decision_input, public_source_ids)
        case reviewer_id:
            return _reviewer_decision(hunk, reviewer_id, public_source_ids)


def _custom_decision(
    hunk: MergeHunk,
    decision_input: _DecisionInput,
    public_source_ids: frozenset[str] | None,
) -> MergeDecision:
    if decision_input.custom_text is None:
        raise ValueError(f"Hunk {hunk.hunk_id!r}: chosen='custom' requires 'custom_text'.")

    final_claims = hunk.base_claims
    if decision_input.custom_source_ids:
        _validate_public_source_ids(
            hunk.hunk_id, "custom source_ids", decision_input.custom_source_ids, public_source_ids
        )
        final_claims = (
            MergeClaim(text=decision_input.custom_text, source_ids=decision_input.custom_source_ids),
        )

    return MergeDecision(
        hunk_id=hunk.hunk_id,
        chosen="custom",
        final_text=decision_input.custom_text,
        final_claims=final_claims,
    )


def _reviewer_decision(
    hunk: MergeHunk, reviewer_id: str, public_source_ids: frozenset[str] | None
) -> MergeDecision:
    variant = next((candidate for candidate in hunk.variants if candidate.reviewer_id == reviewer_id), None)
    if variant is None:
        raise ValueError(
            f"Hunk {hunk.hunk_id!r}: reviewer {reviewer_id!r} not found in variants. "
            f"Available: {[candidate.reviewer_id for candidate in hunk.variants]!r}"
        )

    final_claims = hunk.base_claims
    if variant.source_ids:
        _validate_public_source_ids(hunk.hunk_id, "variant source_ids", variant.source_ids, public_source_ids)
        final_claims = (MergeClaim(text=variant.text, source_ids=variant.source_ids),)

    return MergeDecision(
        hunk_id=hunk.hunk_id,
        chosen=reviewer_id,
        final_text=variant.text,
        final_claims=final_claims,
    )


def _validate_public_source_ids(
    hunk_id: str,
    source_label: str,
    source_ids: tuple[str, ...],
    public_source_ids: frozenset[str] | None,
) -> None:
    if public_source_ids is None:
        return

    invalid_source_ids = tuple(source_id for source_id in source_ids if source_id not in public_source_ids)
    if invalid_source_ids:
        raise ValueError(f"Hunk {hunk_id!r}: {source_label} {invalid_source_ids!r} not PUBLIC.")


__all__ = [
    "_DecisionInput",
    "resolve_decisions",
    "resolve_decisions_from_file",
    "resolve_decisions_from_stdin",
]
