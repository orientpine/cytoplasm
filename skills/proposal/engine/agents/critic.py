from __future__ import annotations

import json
import re
from typing import cast

from .llm import CacheMissError
from ..contracts import Claim, CriticReport, IR, KPI, Rejection, SectionDraft
from ..contracts.protocols import LLMClient, PMSQuery
from ..contracts.validators import (
    validate_citation_cover,
    validate_kpi_sum,
    validate_sections,
)


RUBRIC_CRITERIA: tuple[str, ...] = (
    "citation_cover",
    "kpi_sum",
    "sections",
    "trl_monotonic",
    "keyword_count",
    "no_orphan_claims",
    "no_conflict_evidence",
)

_RULE_MD_CRITERIA: tuple[str, ...] = (
    "1. 구조 및 형식 완성도",
    "2. 논리적 일관성",
    "3. 구체성 및 명확성",
    "4. 양식 일관성",
    "5. 참고문헌 신뢰도",
    "6. 간결성 및 전달력",
    "7. 단어 적절성 및 문체 안정성",
)
_CLAIM_CELL_RE: re.Pattern[str] = re.compile(r"^sec\d+\.claim\d+$")
_IR_CELL_RE: re.Pattern[str] = re.compile(r"^ir\.[A-Za-z0-9_]+$")


def critique(
    ir: IR,
    drafts: list[SectionDraft],
    pms: PMSQuery,
    hwpx_path: str | None = None,
    llm: LLMClient | None = None,
) -> CriticReport:
    _ = hwpx_path
    rubric_scores: dict[str, float] = {}
    rejections: list[Rejection] = []

    citation_report = validate_citation_cover(drafts, pms)
    citation_passed = citation_report.coverage == 1.0 and citation_report.orphans == 0
    rubric_scores["citation_cover"] = _score(citation_passed)
    if not citation_passed:
        rejections.extend(
            _citation_cover_rejections(
                drafts,
                pms,
                reason=(
                    "citation coverage must be 1.0 with zero orphans; "
                    f"coverage={citation_report.coverage:.2f}, "
                    f"orphans={citation_report.orphans}"
                ),
            )
        )

    try:
        validate_kpi_sum(ir.kpis)
    except ValueError as exc:
        rubric_scores["kpi_sum"] = 0.0
        rejections.append(
            Rejection(
                criterion="kpi_sum",
                cell="ir.kpis",
                reason=str(exc),
            )
        )
    else:
        rubric_scores["kpi_sum"] = 1.0

    missing_sections = validate_sections("\n".join(draft.body for draft in drafts))
    sections_passed = not missing_sections
    rubric_scores["sections"] = _score(sections_passed)
    if not sections_passed:
        rejections.append(
            Rejection(
                criterion="sections",
                cell="drafts.body",
                reason=f"missing required sections: {', '.join(missing_sections)}",
            )
        )

    trl_passed = ir.trl_start < ir.trl_end
    rubric_scores["trl_monotonic"] = _score(trl_passed)
    if not trl_passed:
        rejections.append(
            Rejection(
                criterion="trl_monotonic",
                cell="ir.trl",
                reason=f"trl_start must be less than trl_end; got {ir.trl_start}>={ir.trl_end}",
            )
        )

    keyword_passed = len(ir.keywords) <= 5
    rubric_scores["keyword_count"] = _score(keyword_passed)
    if not keyword_passed:
        rejections.append(
            Rejection(
                criterion="keyword_count",
                cell="ir.keywords",
                reason=f"keywords must contain at most 5 entries; got {len(ir.keywords)}",
            )
        )

    orphan_rejections = _orphan_claim_rejections(drafts)
    rubric_scores["no_orphan_claims"] = _score(not orphan_rejections)
    rejections.extend(orphan_rejections)

    conflict_rejections = _conflict_evidence_rejections(drafts, pms)
    rubric_scores["no_conflict_evidence"] = _score(not conflict_rejections)
    rejections.extend(conflict_rejections)

    if llm is not None:
        rejections = _merge_rejections(_llm_rejections(ir, drafts, pms, llm), rejections)

    passed_rubrics = sum(rubric_scores[criterion] for criterion in RUBRIC_CRITERIA)
    return CriticReport(
        rubric_scores=rubric_scores,
        rejections=rejections,
        overall_score=passed_rubrics / len(RUBRIC_CRITERIA),
    )


def _score(passed: bool) -> float:
    return 1.0 if passed else 0.0


def _citation_cover_rejections(
    drafts: list[SectionDraft],
    pms: PMSQuery,
    *,
    reason: str,
) -> list[Rejection]:
    rejections: list[Rejection] = []
    for section_index, draft in enumerate(drafts):
        for claim_index, claim in enumerate(draft.claims):
            if _claim_has_citation_gap(claim, pms):
                rejections.append(
                    Rejection(
                        criterion="citation_cover",
                        cell=_claim_cell(section_index, claim_index),
                        reason=reason,
                    )
                )
    if rejections:
        return rejections
    return [Rejection(criterion="citation_cover", cell="drafts.claims", reason=reason)]


def _claim_has_citation_gap(claim: Claim, pms: PMSQuery) -> bool:
    is_numeric_claim = any(char.isdigit() for char in claim.text)
    if not claim.source_ids:
        return is_numeric_claim
    return not any(pms.resolve(source_id) is not None for source_id in claim.source_ids)


def _orphan_claim_rejections(drafts: list[SectionDraft]) -> list[Rejection]:
    rejections: list[Rejection] = []
    for section_index, draft in enumerate(drafts):
        for claim_index, claim in enumerate(draft.claims):
            if not claim.source_ids:
                rejections.append(
                    Rejection(
                        criterion="no_orphan_claims",
                        cell=_claim_cell(section_index, claim_index),
                        reason="claim has no source_ids",
                    )
                )
    return rejections


def _conflict_evidence_rejections(drafts: list[SectionDraft], pms: PMSQuery) -> list[Rejection]:
    rejections: list[Rejection] = []
    for section_index, draft in enumerate(drafts):
        for claim_index, claim in enumerate(draft.claims):
            conflict_sources = [
                source_id
                for source_id in claim.source_ids
                if (evidence := pms.resolve(source_id)) is not None and evidence.conflict
            ]
            if conflict_sources:
                rejections.append(
                    Rejection(
                        criterion="no_conflict_evidence",
                        cell=_claim_cell(section_index, claim_index),
                        reason=f"claim uses conflict-flagged evidence: {', '.join(conflict_sources)}",
                    )
                )
    return rejections


def _claim_cell(section_index: int, claim_index: int) -> str:
    return f"sec{section_index}.claim{claim_index}"


def _llm_rejections(
    ir: IR,
    drafts: list[SectionDraft],
    pms: PMSQuery,
    llm: LLMClient,
) -> list[Rejection]:
    try:
        response = llm.complete("critic", _llm_critic_prompt(ir, drafts, pms)).strip()
    except CacheMissError:
        return []
    if not response:
        return []
    return _parse_llm_rejections(response)


def _llm_critic_prompt(ir: IR, drafts: list[SectionDraft], pms: PMSQuery) -> str:
    evidence_lines = [
        f"- {unit.unit_id}: {unit.fact}"
        for unit in sorted(pms.public_evidence(), key=lambda unit: unit.unit_id)
    ]
    draft_lines: list[str] = []
    for section_index, draft in enumerate(drafts):
        claim_lines = [
            _llm_claim_prompt_line(section_index, claim_index, claim)
            for claim_index, claim in enumerate(draft.claims)
        ]
        draft_lines.extend(
            [
                f"section: sec{section_index}",
                f"section_id: {draft.section_id}",
                f"title: {draft.title}",
                f"body: {draft.body}",
                "claims:",
                *claim_lines,
            ]
        )

    prompt = "\n".join(
        [
            "Act as an adversarial reviewer for a KIMM R&D proposal.",
            "Find concrete defects using the rule.md evaluation criteria:",
            *_RULE_MD_CRITERIA,
            "Return JSON only, with this schema:",
            "".join(
                (
                    '{"rejections":[{"criterion":"structure|logic|specificity|format|',
                    'references|conciseness|style","cell":"secN.claimM|ir.field",',
                    '"reason":"concise defect"}]}',
                )
            ),
            (
                "Use only reviser-compatible cells: secN.claimM for section claims, "
                "or ir.* for PlanSpec IR fields."
            ),
            "Reject only actionable defects; do not include markdown or prose outside JSON.",
            f"title: {ir.title}",
            f"trl_start: {ir.trl_start}",
            f"trl_end: {ir.trl_end}",
            f"objectives: {'; '.join(ir.objectives)}",
            f"keywords: {', '.join(ir.keywords)}",
            "kpis:",
            *[_llm_kpi_prompt_line(kpi) for kpi in ir.kpis],
            "public_evidence:",
            *(evidence_lines or ["- NONE"]),
            "drafts:",
            *draft_lines,
        ]
    )
    return prompt


def _llm_claim_prompt_line(section_index: int, claim_index: int, claim: Claim) -> str:
    source_ids = ", ".join(claim.source_ids) or "NONE"
    return f"- sec{section_index}.claim{claim_index}: {claim.text} (source_ids={source_ids})"


def _llm_kpi_prompt_line(kpi: KPI) -> str:
    return "".join(
        (
            f"- {kpi.name}: {kpi.baseline} -> {kpi.target}, weight={kpi.weight}, ",
            f"method={kpi.method}, env={kpi.env}, rationale={kpi.rationale}",
        )
    )


def _parse_llm_rejections(response: str) -> list[Rejection]:
    payload = _decode_json_response(response)
    if isinstance(payload, list):
        raw_rejections = payload
    else:
        raw_rejections_object = payload.get("rejections", [])
        if not isinstance(raw_rejections_object, list):
            return []
        raw_rejections = cast("list[object]", raw_rejections_object)
    if not raw_rejections:
        return []

    rejections: list[Rejection] = []
    for raw_rejection in raw_rejections:
        if not isinstance(raw_rejection, dict):
            continue
        rejection_payload = cast("dict[str, object]", raw_rejection)
        criterion = rejection_payload.get("criterion")
        cell = rejection_payload.get("cell")
        reason = rejection_payload.get("reason")
        if not isinstance(cell, str) or not _is_reviser_cell(cell):
            continue
        if not isinstance(criterion, str) or not criterion.strip():
            criterion = "critic"
        if not isinstance(reason, str) or not reason.strip():
            reason = "LLM adversarial critic rejected this cell"
        rejections.append(
            Rejection(
                criterion=f"llm_{criterion.strip()}",
                cell=cell.strip(),
                reason=reason.strip(),
            )
        )
    return rejections


def _decode_json_response(response: str) -> dict[str, object] | list[object]:
    try:
        parsed = cast("object", json.loads(response))
    except json.JSONDecodeError:
        start = response.find("{")
        end = response.rfind("}")
        if start == -1 or end == -1 or start >= end:
            return {}
        try:
            parsed = cast("object", json.loads(response[start : end + 1]))
        except json.JSONDecodeError:
            return {}
    if isinstance(parsed, dict):
        return cast("dict[str, object]", parsed)
    if isinstance(parsed, list):
        return cast("list[object]", parsed)
    return {}


def _is_reviser_cell(cell: str) -> bool:
    return _IR_CELL_RE.match(cell) is not None or _CLAIM_CELL_RE.match(cell) is not None


def _merge_rejections(
    priority_rejections: list[Rejection],
    fallback_rejections: list[Rejection],
) -> list[Rejection]:
    merged: list[Rejection] = []
    seen: set[tuple[str, str]] = set()
    for rejection in [*priority_rejections, *fallback_rejections]:
        key = (rejection.criterion, rejection.cell)
        if key in seen:
            continue
        seen.add(key)
        merged.append(rejection)
    return merged


__all__ = ["RUBRIC_CRITERIA", "critique"]
