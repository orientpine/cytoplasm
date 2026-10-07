from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from ..contracts.collab_models import (
    MergeClaim,
    MergeHunk,
    MergePlan,
    MergeVariant,
    ReviewerOpinion,
)
from ..contracts.models import Claim, SectionDraft
from ..contracts.protocols import PMSQuery

_BASE_SOURCE_ID: Final[str] = "_base_"
_SEC_BODY_RE: Final[re.Pattern[str]] = re.compile(r"^sec(?P<n>\d+)$")
_SEC_CLAIM_RE: Final[re.Pattern[str]] = re.compile(r"^sec(?P<n>\d+)\.claim(?P<m>\d+)$")
_IR_RE: Final[re.Pattern[str]] = re.compile(r"^ir\.")
_FACT_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"\d+(?:\.\d+)?\s*(?:%|퍼센트|년|개월|억원)?|TRL|KPI")


@dataclass(frozen=True, slots=True)
class _MergeContext:
    draft_by_section: dict[str, SectionDraft]
    opinions_by_locator: dict[str, tuple[ReviewerOpinion, ...]]
    public_source_ids: frozenset[str]


class MergePlanSourceError(ValueError):
    pass


class MergePlanFactChangeError(ValueError):
    pass


def build_merge_plan(
    drafts: list[SectionDraft],
    opinions: list[ReviewerOpinion],
    pms: PMSQuery,
) -> MergePlan:
    """Build a deterministic merge plan from drafts and reviewer opinions."""
    context = _build_context(drafts, opinions, pms)
    hunks = [
        hunk
        for locator in sorted(context.opinions_by_locator, key=_locator_sort_key)
        if (hunk := _build_hunk(locator, context)) is not None
    ]
    return MergePlan(hunks=tuple(hunks))


def _build_context(
    drafts: list[SectionDraft], opinions: list[ReviewerOpinion], pms: PMSQuery
) -> _MergeContext:
    draft_by_section = {draft.section_id: draft for draft in drafts}
    opinions_by_locator: dict[str, list[ReviewerOpinion]] = {}
    for opinion in sorted(opinions, key=_opinion_sort_key):
        if _IR_RE.match(opinion.locator):
            continue
        opinions_by_locator.setdefault(opinion.locator, []).append(opinion)
    return _MergeContext(
        draft_by_section=draft_by_section,
        opinions_by_locator={
            locator: tuple(locator_opinions)
            for locator, locator_opinions in sorted(opinions_by_locator.items())
        },
        public_source_ids=frozenset(unit.unit_id for unit in pms.public_evidence()),
    )


def _build_hunk(locator: str, context: _MergeContext) -> MergeHunk | None:
    sec_match = _SEC_BODY_RE.match(locator)
    if sec_match is not None:
        return _build_section_body_hunk(locator, sec_match.group("n"), context)

    claim_match = _SEC_CLAIM_RE.match(locator)
    if claim_match is not None:
        return _build_claim_hunk(locator, claim_match.group("n"), int(claim_match.group("m")), context)

    return None


def _build_section_body_hunk(locator: str, section_id: str, context: _MergeContext) -> MergeHunk | None:
    draft = context.draft_by_section.get(section_id)
    if draft is None:
        return None

    opinions = context.opinions_by_locator[locator]
    variants = tuple(
        MergeVariant(reviewer_id=opinion.reviewer_id, text=opinion.proposed_edit)
        for opinion in opinions
        if opinion.proposed_edit is not None
    )
    if not variants:
        return None

    _reject_unsupported_fact_changes(section_id, draft, variants, context)
    return MergeHunk(
        hunk_id=_hunk_id(locator, opinions),
        locator=locator,
        base_text=draft.body,
        base_claims=_merge_claims(draft.claims),
        variants=variants,
        conflict=_has_conflict(variants),
    )


def _build_claim_hunk(locator: str, section_id: str, claim_idx: int, context: _MergeContext) -> MergeHunk | None:
    draft = context.draft_by_section.get(section_id)
    if draft is None or claim_idx > len(draft.claims):
        return None

    base_claim = draft.claims[claim_idx] if claim_idx < len(draft.claims) else None
    opinions = context.opinions_by_locator[locator]
    variants = tuple(_claim_variant(locator, opinion, base_claim, context) for opinion in opinions)
    if not variants:
        return None

    return MergeHunk(
        hunk_id=_hunk_id(locator, opinions),
        locator=locator,
        base_text=base_claim.text if base_claim is not None else "",
        base_claims=_merge_claims([base_claim]) if base_claim is not None else (),
        variants=variants,
        conflict=_has_claim_conflict(variants, base_claim),
    )


def _claim_variant(
    locator: str,
    opinion: ReviewerOpinion,
    base_claim: Claim | None,
    context: _MergeContext,
) -> MergeVariant:
    if opinion.proposed_edit is None:
        text = base_claim.text if base_claim is not None else opinion.opinion_text
        return MergeVariant(reviewer_id=opinion.reviewer_id, text=text)

    source_ids = _validated_source_ids(locator, opinion, context.public_source_ids)
    return MergeVariant(
        reviewer_id=opinion.reviewer_id,
        text=opinion.proposed_edit,
        source_ids=source_ids,
    )


def _reject_unsupported_fact_changes(
    section_id: str,
    draft: SectionDraft,
    variants: tuple[MergeVariant, ...],
    context: _MergeContext,
) -> None:
    base_fact_tokens = _fact_tokens(draft.body, tuple(claim.text for claim in draft.claims))
    for variant in variants:
        variant_fact_tokens = _FACT_MARKER_RE.findall(variant.text)
        if not variant_fact_tokens or set(variant_fact_tokens) <= base_fact_tokens:
            continue
        if _has_public_claim_edit(section_id, context):
            continue
        raise MergePlanFactChangeError(
            f"fact-change guard rejected {section_id!r}: body edit by {variant.reviewer_id!r} "
            "changes factual tokens without a corresponding sec<N>.claimM edit carrying "
            "PUBLIC source_ids"
        )


def _has_public_claim_edit(section_id: str, context: _MergeContext) -> bool:
    prefix = f"sec{section_id}.claim"
    for locator, opinions in context.opinions_by_locator.items():
        if not locator.startswith(prefix):
            continue
        for opinion in opinions:
            if opinion.proposed_edit is None:
                continue
            _validated_source_ids(locator, opinion, context.public_source_ids)
            return True
    return False


def _validated_source_ids(
    locator: str, opinion: ReviewerOpinion, public_source_ids: frozenset[str]
) -> tuple[str, ...]:
    if opinion.source_ids is None or not opinion.source_ids:
        raise MergePlanSourceError(
            f"Claim opinion at {locator!r} from {opinion.reviewer_id!r} changes claim text "
            "but provides no source_ids. source_ids is REQUIRED for claim edits."
        )

    invalid_source_ids = tuple(
        source_id for source_id in opinion.source_ids if source_id not in public_source_ids
    )
    if invalid_source_ids:
        raise MergePlanSourceError(
            f"Claim opinion at {locator!r} from {opinion.reviewer_id!r} references "
            f"non-PUBLIC source_ids: {invalid_source_ids!r}. Only PUBLIC evidence can be cited."
        )
    return opinion.source_ids


def _merge_claims(claims: list[Claim]) -> tuple[MergeClaim, ...]:
    return tuple(
        MergeClaim(text=claim.text, source_ids=tuple(claim.source_ids) or (_BASE_SOURCE_ID,))
        for claim in claims
    )


def _fact_tokens(body: str, claim_texts: tuple[str, ...]) -> set[str]:
    return set(_FACT_MARKER_RE.findall("\n".join((body, *claim_texts))))


def _has_conflict(variants: tuple[MergeVariant, ...]) -> bool:
    return len(variants) >= 2 and len({variant.text for variant in variants}) > 1


def _has_claim_conflict(variants: tuple[MergeVariant, ...], base_claim: Claim | None) -> bool:
    base_text = base_claim.text if base_claim is not None else ""
    changed_texts = {variant.text for variant in variants if variant.text != base_text}
    return len(variants) >= 2 and len(changed_texts) > 1


def _hunk_id(locator: str, opinions: tuple[ReviewerOpinion, ...]) -> str:
    reviewer_ids = ":".join(opinion.reviewer_id for opinion in opinions)
    return f"{locator}:{reviewer_ids}"


def _opinion_sort_key(opinion: ReviewerOpinion) -> tuple[str, str]:
    return opinion.reviewer_id, opinion.locator


def _locator_sort_key(locator: str) -> tuple[int, int, int, str]:
    sec_match = _SEC_BODY_RE.match(locator)
    if sec_match is not None:
        return 0, int(sec_match.group("n")), -1, locator

    claim_match = _SEC_CLAIM_RE.match(locator)
    if claim_match is not None:
        return 0, int(claim_match.group("n")), int(claim_match.group("m")), locator

    return 1, 0, 0, locator


__all__ = ["build_merge_plan"]
