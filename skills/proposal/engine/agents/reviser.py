from __future__ import annotations

import re
from collections.abc import Mapping
from math import floor
from typing import cast

from .critic import critique
from ..contracts import Claim, CriticReport, EvidenceUnit, IR, KPI, Rejection, SectionDraft
from ..contracts.protocols import LLMClient, PMSQuery


_CLAIM_CELL_RE: re.Pattern[str] = re.compile(
    r"^sec(?P<section_id>\d+)\.claim(?P<claim_index>\d+)$"
)
_WORD_RE: re.Pattern[str] = re.compile(r"[0-9A-Za-z가-힣]+")
MAX_REVISE_ROUNDS = 2


def revise(
    drafts: list[SectionDraft],
    ir_content: dict[str, object],
    critic_report: CriticReport,
    pms: PMSQuery,
    llm: LLMClient,
) -> tuple[list[SectionDraft], dict[str, object]]:
    revised_drafts = list(drafts)
    revised_ir_content = _copy_ir_content(ir_content)

    for rejection in critic_report.rejections:
        if rejection.cell.startswith("ir."):
            revised_ir_content = _revise_ir_content(revised_ir_content, rejection)
            continue

        target = _parse_claim_cell(rejection.cell)
        if target is None:
            continue

        section_id, claim_index = target
        draft_index = _find_draft_index(revised_drafts, section_id)
        if draft_index is None:
            continue

        draft = revised_drafts[draft_index]
        if claim_index >= len(draft.claims):
            continue

        claim = draft.claims[claim_index]
        evidence = _find_grounding_evidence(claim, pms)
        revised_claim = Claim(
            text=_rephrase_claim(claim, evidence.fact, rejection, llm),
            source_ids=[evidence.unit_id],
        )
        claims = list(draft.claims)
        claims[claim_index] = revised_claim
        revised_drafts[draft_index] = draft.model_copy(update={"claims": claims})

    after_report = critique(_ir_from_content(revised_ir_content), revised_drafts, pms)
    assert after_report.overall_score > critic_report.overall_score or len(
        after_report.rejections
    ) < len(critic_report.rejections)
    return revised_drafts, revised_ir_content


def _copy_ir_content(ir_content: object) -> dict[str, object]:
    if not isinstance(ir_content, dict):
        raise TypeError("ir_content must be a mutable dict, not a frozen PlanSpec or IR model")

    copied: dict[str, object] = {}
    mapping = cast("Mapping[object, object]", ir_content)
    for key, value in mapping.items():
        if not isinstance(key, str):
            raise TypeError("ir_content keys must be strings")
        copied[key] = value
    return copied


def _revise_ir_content(ir_content: dict[str, object], rejection: Rejection) -> dict[str, object]:
    revised = dict(ir_content)
    field = rejection.cell.removeprefix("ir.")

    if field == "kpis":
        revised["kpis"] = _normalized_kpis(revised.get("kpis", []))
    elif field == "keywords":
        keywords = revised.get("keywords")
        if isinstance(keywords, list):
            revised["keywords"] = keywords[:5]
    elif field in {"trl", "trl_start", "trl_end"}:
        trl_start = _int_value(revised.get("trl_start"), 0)
        trl_end = _int_value(revised.get("trl_end"), trl_start)
        if trl_start >= trl_end:
            revised["trl_end"] = trl_start + 1

    return revised


def _normalized_kpis(raw_kpis: object) -> list[dict[str, object]]:
    if not isinstance(raw_kpis, list):
        raise TypeError("ir.kpis must be a list")
    raw_items = cast("list[object]", raw_kpis)
    kpis: list[KPI] = [KPI.model_validate(raw_kpi) for raw_kpi in raw_items]
    return [_dump_kpi(kpi) for kpi in _normalize_kpi_weights(kpis)]


def _normalize_kpi_weights(kpis: list[KPI]) -> list[KPI]:
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


def _dump_kpi(kpi: KPI) -> dict[str, object]:
    return {
        "name": kpi.name,
        "unit": kpi.unit,
        "baseline": kpi.baseline,
        "target": kpi.target,
        "weight": kpi.weight,
        "method": kpi.method,
        "env": kpi.env,
        "rationale": kpi.rationale,
    }


def _int_value(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return default


def _parse_claim_cell(cell: str) -> tuple[int, int] | None:
    match = _CLAIM_CELL_RE.match(cell)
    if match is None:
        return None
    return int(match.group("section_id")), int(match.group("claim_index"))


def _find_draft_index(drafts: list[SectionDraft], section_id: int) -> int | None:
    section_label = str(section_id)
    for index, draft in enumerate(drafts):
        if draft.section_id == section_label:
            return index
    if section_id < len(drafts):
        return section_id
    return None


def _find_grounding_evidence(claim: Claim, pms: PMSQuery) -> EvidenceUnit:
    candidates = [unit for unit in pms.public_evidence() if not unit.conflict]
    if not candidates:
        raise ValueError("No public non-conflict evidence available for revision")

    tokens = _topic_tokens(claim.text)
    scored = [(_coverage_score(claim.text, tokens, unit.fact), unit) for unit in candidates]
    scored.sort(key=lambda item: (-item[0], item[1].unit_id))
    best_score, best_unit = scored[0]
    if best_score <= 0:
        return candidates[0]
    return best_unit


def _topic_tokens(text: str) -> set[str]:
    tokens: list[str] = _WORD_RE.findall(text)
    return {token.casefold() for token in tokens if len(token) > 1}


def _coverage_score(claim_text: str, tokens: set[str], evidence_fact: str) -> int:
    claim_folded = claim_text.casefold()
    fact_folded = evidence_fact.casefold()
    score = sum(1 for token in tokens if token in fact_folded)
    if claim_folded and (claim_folded in fact_folded or fact_folded in claim_folded):
        score += 10
    return score


def _rephrase_claim(claim: Claim, evidence_fact: str, rejection: Rejection, llm: LLMClient) -> str:
    prompt = _reviser_prompt(claim, evidence_fact, rejection)
    try:
        revised = llm.complete("reviser", prompt).strip()
    except Exception:
        revised = ""
    return revised or evidence_fact


def _reviser_prompt(claim: Claim, evidence_fact: str, rejection: Rejection) -> str:
    return "\n".join(
        [
            "Rephrase the rejected claim using only the provided PMS evidence.",
            f"criterion: {rejection.criterion}",
            f"reason: {rejection.reason}",
            f"claim: {claim.text}",
            f"evidence: {evidence_fact}",
            "Return one concise claim.",
        ]
    )


def _ir_from_content(ir_content: dict[str, object]) -> IR:
    return IR.model_validate(ir_content)


__all__ = ["MAX_REVISE_ROUNDS", "revise"]
