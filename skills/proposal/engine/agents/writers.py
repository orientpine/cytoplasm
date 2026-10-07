from __future__ import annotations

from ..contracts import (
    Claim,
    EvidenceUnit,
    PlanSpec,
    SectionDraft,
)
from ..contracts.protocols import LLMClient, PMSQuery


def write_section(
    section_id: int, planspec: PlanSpec, pms: PMSQuery, llm: LLMClient
) -> SectionDraft:
    writers = {
        0: _write_summary,
        1: _write_background,
        2: _write_objectives,
        3: _write_methods,
        4: _write_impact,
    }
    if section_id not in writers:
        raise ValueError(f"Unknown section_id: {section_id}")
    return writers[section_id](planspec, pms, llm)


def write_all(planspec: PlanSpec, pms: PMSQuery, llm: LLMClient) -> list[SectionDraft]:
    return [write_section(i, planspec, pms, llm) for i in range(5)]


def _get_grounding_evidence(
    pms: PMSQuery, planspec: PlanSpec, slots: list[str] | None = None
) -> list[EvidenceUnit]:
    public_units = pms.public_evidence()
    public_ids = {u.unit_id for u in public_units}

    if slots is None:
        return [u for u in public_units if not u.conflict]

    unit_ids: set[str] = set()
    for link in planspec.traceability.links:
        if link.ir_slot in slots:
            unit_ids.update(link.evidence_unit_ids)

    units = []
    for uid in sorted(unit_ids):
        if uid in public_ids:
            u = pms.resolve(uid)
            if u and not u.conflict:
                units.append(u)
    return units


def _build_prompt(section_id: int, planspec: PlanSpec, evidence: list[EvidenceUnit]) -> str:
    evidence_summary = "\n".join(f"- {u.unit_id}: {u.fact}" for u in evidence)
    return (
        f"Write section {section_id} for proposal:\n"
        f"Title: {planspec.title}\n"
        f"Objectives: {', '.join(planspec.objectives)}\n"
        f"Evidence: {evidence_summary}"
    )


def _create_draft(
    section_id: int, title: str, body: str, evidence: list[EvidenceUnit]
) -> SectionDraft:
    claims = []
    for u in evidence:
        if u.fact in body:
            claims.append(Claim(text=u.fact, source_ids=[u.unit_id]))
    return SectionDraft(
        section_id=str(section_id),
        title=title,
        body=body,
        claims=claims,
    )


def _write_summary(planspec: PlanSpec, pms: PMSQuery, llm: LLMClient) -> SectionDraft:
    evidence = _get_grounding_evidence(pms, planspec, ["objectives", "trl_start", "trl_end", "kpis"])
    prompt = _build_prompt(0, planspec, evidence)
    body = llm.complete("writer_sec0", prompt)
    return _create_draft(0, "요약문", body, evidence)


def _write_background(planspec: PlanSpec, pms: PMSQuery, llm: LLMClient) -> SectionDraft:
    evidence = _get_grounding_evidence(pms, planspec, ["objectives"])
    prompt = _build_prompt(1, planspec, evidence)
    body = llm.complete("writer_sec1", prompt)
    return _create_draft(1, "배경·필요성", body, evidence)


def _write_objectives(planspec: PlanSpec, pms: PMSQuery, llm: LLMClient) -> SectionDraft:
    evidence = _get_grounding_evidence(pms, planspec, ["objectives", "kpis"])
    prompt = _build_prompt(2, planspec, evidence)
    body = llm.complete("writer_sec2", prompt)
    return _create_draft(2, "연구목표", body, evidence)


def _write_methods(planspec: PlanSpec, pms: PMSQuery, llm: LLMClient) -> SectionDraft:
    evidence = _get_grounding_evidence(pms, planspec, ["methodology", "work_packages"])
    prompt = _build_prompt(3, planspec, evidence)
    body = llm.complete("writer_sec3", prompt)
    return _create_draft(3, "연구내용·방법", body, evidence)


def _write_impact(planspec: PlanSpec, pms: PMSQuery, llm: LLMClient) -> SectionDraft:
    evidence = _get_grounding_evidence(pms, planspec, ["kpis"])
    prompt = _build_prompt(4, planspec, evidence)
    body = llm.complete("writer_sec4", prompt)
    return _create_draft(4, "기대효과", body, evidence)
