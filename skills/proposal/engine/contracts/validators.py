from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import floor
from typing import Protocol, TypeAlias

from ..agents.kimm_domain import KIMM_DOMAIN, KIMMDomainPack

from .layout_profile import LEGACY_LAYOUT_PROFILE_NAME, LayoutProfile, get_layout_profile
from .models import FrozenModel, PlanSpec


class _CitationStatusLike(Protocol):
    value: str


class _KPILike(Protocol):
    weight: int

    def model_copy(self, *, update: dict[str, int]) -> "KPI": ...


class _ProvenanceLike(Protocol):
    source_id: str


class _EvidenceUnitLike(Protocol):
    @property
    def provenances(self) -> Sequence[_ProvenanceLike]: ...


class _PMSQueryLike(Protocol):
    def public_evidence(self) -> Sequence[_EvidenceUnitLike]: ...

    def resolve(self, source_id: str) -> _EvidenceUnitLike | None: ...


class _ClaimLike(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def source_ids(self) -> Sequence[str]: ...


class _SectionDraftLike(Protocol):
    @property
    def claims(self) -> Sequence[_ClaimLike]: ...


CitationStatus: TypeAlias = str | _CitationStatusLike
KPI: TypeAlias = _KPILike
PMSQuery: TypeAlias = _PMSQueryLike
SectionDraft: TypeAlias = _SectionDraftLike


# Backward-compatible alias for callers that still consume the original 10-page budgets.
budget_constants: dict[int, int] = dict(
    get_layout_profile(LEGACY_LAYOUT_PROFILE_NAME).prose_budgets
)

DEFAULT_REQUIRED_SECTIONS: list[str] = [
    "요약문",
    "배경·필요성",
    "연구목표",
    "연구내용·방법",
    "기대효과",
]


@dataclass(frozen=True)
class CoverReport:
    ok: bool
    coverage: float
    orphans: int
    missing_sources: list[str]


def validate_kpi_sum(kpis: Sequence[KPI]) -> None:
    total_weight = sum(kpi.weight for kpi in kpis)
    if total_weight != 100:
        raise ValueError("KPI weights must sum to 100")


def normalize_kpi_weights(kpis: Sequence[KPI]) -> list[KPI]:
    total_weight = sum(kpi.weight for kpi in kpis)
    if total_weight <= 0:
        raise ValueError("KPI weights must have a positive total")

    proportional = [(kpi.weight / total_weight) * 100 for kpi in kpis]
    floors = [floor(weight) for weight in proportional]
    remainder = 100 - sum(floors)

    fractional_order = sorted(
        range(len(kpis)),
        key=lambda index: (proportional[index] - floors[index], kpis[index].weight),
        reverse=True,
    )
    adjusted = floors[:]
    for index in fractional_order[:remainder]:
        adjusted[index] += 1

    return [kpi.model_copy(update={"weight": weight}) for kpi, weight in zip(kpis, adjusted, strict=True)]


def validate_citation_cover(
    drafts: Sequence[SectionDraft],
    pms: PMSQuery,
    allowed: tuple[CitationStatus, ...] | None = None,
) -> CoverReport:
    allowed_statuses = {"PUBLIC"} if allowed is None else {getattr(status, "value", status) for status in allowed}
    public_source_ids = {
        provenance.source_id
        for evidence in pms.public_evidence()
        for provenance in evidence.provenances
    }

    checked_claims = 0
    covered_claims = 0
    orphans = 0
    missing_sources: list[str] = []

    for draft in drafts:
        for claim in draft.claims:
            is_numeric_claim = any(char.isdigit() for char in claim.text)
            if not claim.source_ids and not is_numeric_claim:
                continue

            checked_claims += 1
            if not claim.source_ids:
                orphans += 1
                continue

            resolved_allowed = False
            claim_missing: list[str] = []
            for source_id in claim.source_ids:
                evidence = pms.resolve(source_id)
                if evidence is None:
                    claim_missing.append(source_id)
                    continue

                status = getattr(evidence, "citation_status", None) or getattr(evidence, "status", None)
                if status is None and source_id in public_source_ids:
                    status = "PUBLIC"

                if getattr(status, "value", status) in allowed_statuses:
                    resolved_allowed = True
                else:
                    claim_missing.append(source_id)

            if resolved_allowed:
                covered_claims += 1
            else:
                orphans += 1
                missing_sources.extend(claim_missing or claim.source_ids)

    coverage = 1.0 if checked_claims == 0 else covered_claims / checked_claims
    return CoverReport(
        ok=orphans == 0,
        coverage=coverage,
        orphans=orphans,
        missing_sources=missing_sources,
    )


def validate_sections(text: str, required: list[str] | None = None) -> list[str]:
    required_sections = DEFAULT_REQUIRED_SECTIONS if required is None else required
    return [section for section in required_sections if section not in text]


@dataclass(frozen=True)
class NumericViolation(FrozenModel):
    check: str
    detail: str


@dataclass(frozen=True)
class NumericValidationReport(FrozenModel):
    violations: tuple[NumericViolation, ...]
    ok: bool


def _leading_int(text: str) -> int | None:
    digits: list[str] = []
    for char in text:
        if char.isdigit():
            digits.append(char)
        elif digits:
            break
    return int("".join(digits)) if digits else None


def validate_numeric_consistency(
    planspec: PlanSpec,
    domain: KIMMDomainPack = KIMM_DOMAIN,
    *,
    profile: LayoutProfile | str | None = None,
) -> NumericValidationReport:
    violations: list[NumericViolation] = []

    try:
        validate_kpi_sum(planspec.kpis)
    except ValueError as exc:
        violations.append(NumericViolation(check="kpi_sum", detail=str(exc)))

    req = domain.trl_requirement
    start, end = planspec.trl_start, planspec.trl_end
    if req.must_increase and start >= end:
        violations.append(
            NumericViolation(
                check="trl_monotonic",
                detail=f"trl_start ({start}) must be < trl_end ({end})",
            )
        )
    if start < req.start_min:
        violations.append(
            NumericViolation(
                check="trl_monotonic",
                detail=f"trl_start ({start}) below domain minimum {req.start_min}",
            )
        )
    if end < req.end_min:
        violations.append(
            NumericViolation(
                check="trl_monotonic",
                detail=f"trl_end ({end}) below domain minimum {req.end_min}",
            )
        )
    for kpi in planspec.kpis:
        if "TRL" not in kpi.name:
            continue
        baseline = _leading_int(kpi.baseline)
        target = _leading_int(kpi.target)
        if baseline is not None and target is not None and baseline >= target:
            violations.append(
                NumericViolation(
                    check="trl_monotonic",
                    detail=(
                        f"TRL KPI '{kpi.name}' baseline ({baseline}) "
                        f"must be < target ({target})"
                    ),
                )
            )

    if isinstance(profile, LayoutProfile):
        active_profile = profile
    elif profile is not None:
        active_profile = get_layout_profile(profile)
    else:
        legacy_profile = get_layout_profile(LEGACY_LAYOUT_PROFILE_NAME)
        legacy_budget = {
            str(section): budget for section, budget in legacy_profile.prose_budgets.items()
        }
        active_profile = (
            legacy_profile if planspec.page_budget == legacy_budget else get_layout_profile()
        )
    expected_total = sum(active_profile.prose_budgets.values())
    actual_total = sum(planspec.page_budget.values())
    if actual_total != expected_total:
        violations.append(
            NumericViolation(
                check="budget_sum",
                detail=f"page_budget total {actual_total} != expected {expected_total}",
            )
        )

    months = [wp.months for wp in planspec.work_packages]
    if months:
        mean = sum(months) / len(months)
        if mean > 0:
            max_dev = max(abs(month - mean) / mean for month in months)
            limit = domain.budget_schema.max_annual_variance_pct
            if max_dev > limit:
                violations.append(
                    NumericViolation(
                        check="budget_variance",
                        detail=(
                            f"work-package months variance {max_dev:.4f} "
                            f"exceeds limit {limit}"
                        ),
                    )
                )

    return NumericValidationReport(
        violations=tuple(sorted(violations, key=lambda v: v.check)),
        ok=len(violations) == 0,
    )
