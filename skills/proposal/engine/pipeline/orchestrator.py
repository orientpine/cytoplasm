from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ..agents.critic import critique
from ..agents.kimm_domain import KIMM_DOMAIN
from ..agents.llm import CachingLLMClient, MockLLMClient, load_mock_responses
from ..agents.merge_engine import build_merge_plan
from ..agents.parallel_reviewers import ReviewerPersona, run_parallel_reviewers
from ..agents.reviser import revise
from ..agents.rubric_check import check_rubric, normalize_spellings
from ..agents.style_lint import check_forbidden_expressions, compress, lint
from ..agents.writers import write_all
from ..contract.planner import InsufficientEvidenceError, plan
from ..contracts.collab_models import (
    MergePlan,
    RevisionTranscript,
    SectionOpinionEntry,
    TranscriptSection,
)
from ..contracts.ids import stable_id
from ..contracts.layout_profile import (
    LAYOUT_PROFILES,
    LEGACY_LAYOUT_PROFILE_NAME,
    LayoutProfile,
    get_layout_profile,
)
from ..contracts.models import (
    CriticReport,
    EvidenceUnit,
    NodeLog,
    PlanSpec,
    RunResult,
    SectionDraft,
)
from ..contracts.protocols import LLMClient
from ..contracts.validators import validate_numeric_consistency
from ..converter.ingest import RawDoc, ingest_dir
from ..converter.materialize import materialize
from ..converter.normalize import normalize
from ..converter.pms import ProposalMaterialStore
from ..converter.sanitize import sanitize_gate
from ..converter.traceability_graph import build_evidence_graph, traceability_markdown
from ..hwpx.anchor_map import load_anchor_map
from ..hwpx.figure_density import (
    FigureDensityError,
    build_layout_bands,
    load_figure_specs,
    render_body_paragraphs,
    render_layout_bands,
    validate_layout_bands,
)
from ..hwpx._image_contract import MAX_DISPLAY_WIDTH
from ..hwpx.image_embed import ImageSpec, embed_images
from ..hwpx.seed_fill import (
    body_anchor_slot,
    fill_seed_cover,
    remove_seed_guidance,
    trim_trailing_blank_paragraphs,
    restyle_seed_form_runs,
)
from ..hwpx.typography import apply_compact_table_runs, install_form_typography
from ..hwpx.table_fit import fit_tables
from ..hwpx.table_writer import (
    GANTT_MONTHS,
    ComparisonKind,
    GanttRow,
    TableData,
    gantt_table_id,
    insert_comparison_table,
    validate_gantt_years,
    write_gantt,
    write_gantt_years,
    write_kpi_table,
)
from ..hwpx.validate import text_extract, validate_public_artifact
from ..hwpx.xml_writer import sync_prv_text
from ..hwpx.zip_surgery import repack, replace_entry, unpack

from .schedule import gantt_rows as _gantt_rows, _plan_phases as _plan_phases


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LLM_FIXTURE = PROJECT_ROOT / "resource" / "llm-responses.json"
SEED_HWPX = PROJECT_ROOT / "resource" / "R&D 연구계획서 양식.hwpx"
SECTION_ENTRY = "Contents/section0.xml"
HEADER_ENTRY = "Contents/header.xml"
MAX_REVISE_ROUNDS = 2
_SECTION_LOCATOR_RE = re.compile(r"^sec(?P<n>\d+)$")
NODE_NAMES = (
    "ingest",
    "normalize",
    "materialize",
    "pms",
    "planner",
    "writers",
    "style_lint",
    "critic",
    "reviser",
    "render",
    "emit_citation_sidecar",
    "sanitize_gate",
    "validate_public_artifact",
)
VALID_PROFILES = frozenset(LAYOUT_PROFILES)


@dataclass(frozen=True, slots=True)
class DraftResult:
    drafts_path: str
    planspec_path: str
    pms_path: str
    node_log: list[NodeLog]


@dataclass(frozen=True, slots=True)
class RenderContext:
    """Inputs consumed at the HWPX rendering boundary."""

    profile: LayoutProfile
    images_dir: Path | None
    figures_path: Path | None
    tables_path: Path | None = None
    cover_overrides: tuple[tuple[str, str], ...] = ()
    figure_width_hwp: int = MAX_DISPLAY_WIDTH


def run(
    corpus_dir: str,
    out_path: str,
    llm: LLMClient | None = None,
    *,
    project: str = "",
) -> RunResult:
    """Convenience wrapper preserving the original corpus-to-HWPX contract."""
    draft_result = draft(
        corpus_dir=corpus_dir,
        out_path=f"{out_path}.drafts.json",
        llm=llm,
    )
    render_result = render(
        drafts_path=draft_result.drafts_path,
        corpus_dir=corpus_dir,
        out_path=out_path,
        project=project,
    )
    node_log = [*draft_result.node_log, *render_result.node_log]
    if tuple(node.node_name for node in node_log) != NODE_NAMES:
        raise RuntimeError("Pipeline node order is out of sync with NODE_NAMES")
    return render_result.model_copy(update={"node_log": node_log})


def draft(corpus_dir: str, out_path: str, llm: LLMClient | None = None) -> DraftResult:
    """Run corpus ingestion through revision and persist an editable draft bundle."""
    from .draft_bundle import save_draft_file, save_planspec

    resolved_llm = llm or MockLLMClient(load_mock_responses(str(DEFAULT_LLM_FIXTURE)))
    active_profile = get_layout_profile(
        os.environ.get("KIMM_DOCBOT_PROFILE", LEGACY_LAYOUT_PROFILE_NAME)
    )
    node_log: list[NodeLog] = []

    raw_docs: list[RawDoc] = ingest_dir(corpus_dir)
    if not raw_docs:
        raise ValueError("Empty corpus")
    _append_log(node_log, "ingest", corpus_dir, raw_docs)

    docs = [normalize(raw) for raw in raw_docs]
    _append_log(node_log, "normalize", raw_docs, docs)

    units = materialize(docs)
    _append_log(node_log, "materialize", docs, units)

    pms = ProposalMaterialStore(units)
    bundle_base = _bundle_base_for_drafts(out_path)
    pms_path = f"{bundle_base}.pms.json"
    pms_payload = _save_pms_snapshot(pms, pms_path)
    _append_log(node_log, "pms", units, pms_payload)

    planspec = plan(pms, resolved_llm, profile=active_profile)
    _append_log(node_log, "planner", _pms_unit_ids(pms), planspec)

    drafts = write_all(planspec, pms, resolved_llm)
    _append_log(node_log, "writers", planspec, drafts)

    section_missing = _check_section_requirements(drafts)
    if section_missing:
        print(
            json.dumps(
                {"event": "section_requirements_missing", "missing": section_missing},
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    drafts = _style_lint_and_prepare(drafts, active_profile)
    _append_log(node_log, "style_lint", drafts, drafts)

    critic_llm = resolved_llm if isinstance(resolved_llm, CachingLLMClient) else None
    critic_report = critique(planspec, drafts, pms, llm=critic_llm)
    _append_log(node_log, "critic", drafts, critic_report)

    drafts, ir_content = _revise_or_keep(
        drafts,
        planspec,
        critic_report,
        pms,
        resolved_llm,
        critic_llm,
    )
    _append_log(node_log, "reviser", critic_report, {"drafts": drafts, "ir_content": ir_content})

    render_planspec = PlanSpec.model_validate(ir_content)
    planspec_path = save_planspec(bundle_base, render_planspec)
    drafts_path = save_draft_file(out_path, drafts)
    return DraftResult(
        drafts_path=drafts_path,
        planspec_path=planspec_path,
        pms_path=pms_path,
        node_log=node_log,
    )


def render(
    drafts_path: str,
    corpus_dir: str,
    out_path: str,
    *,
    profile: str | None = None,
    images_dir: Path | None = None,
    figures_path: Path | None = None,
    tables_path: Path | None = None,
    cover_overrides: Mapping[str, str] | None = None,
    project: str = "",
) -> RunResult:
    """Render an editable draft bundle with corpus-backed citation validation.

    ``RenderContext`` is passed into ``_render`` as the stable integration seam
    for profile layout, image discovery, and figure-manifest consumers.
    """
    from .draft_bundle import (
        load_drafts,
        load_planspec,
        save_drafts,
        save_planspec,
    )

    active_profile = get_layout_profile(
        profile
        if profile is not None
        else os.environ.get("KIMM_DOCBOT_PROFILE", LEGACY_LAYOUT_PROFILE_NAME)
    )

    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    context = RenderContext(
        profile=active_profile,
        images_dir=images_dir,
        figures_path=figures_path,
        tables_path=tables_path,
        cover_overrides=tuple(
            sorted((key, normalize_spellings(value)) for key, value in (cover_overrides or {}).items())
        ),
    )
    bundle_base = _bundle_base_for_drafts(drafts_path)
    drafts = load_drafts(drafts_path)
    planspec = load_planspec(f"{bundle_base}.planspec.json")

    raw_docs = ingest_dir(corpus_dir)
    if not raw_docs:
        raise ValueError("Empty corpus")
    pms = ProposalMaterialStore(materialize([normalize(raw) for raw in raw_docs]))

    numeric_report = validate_numeric_consistency(planspec, profile=active_profile)
    if not numeric_report.ok:
        print(
            json.dumps(
                {
                    "event": "numeric_validation_violations",
                    "violations": [
                        {"check": v.check, "detail": v.detail} for v in numeric_report.violations
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    drafts = _correct_proposal_drafts(
        drafts,
        _all_units(pms),
        label=planspec.title,
        project=project,
    )
    drafts, planspec = _standard_spellings(drafts, planspec, _all_units(pms))
    _write_rubric_findings(out_path, drafts, planspec, dict(context.cover_overrides))

    drafts = [_without_section_marker(draft) for draft in drafts]
    node_log: list[NodeLog] = []
    rendered_text = _render(out_path, planspec, drafts, pms, context=context)
    _append_log(
        node_log,
        "render",
        {"planspec": planspec, "drafts": drafts, "context": context},
        stable_id(rendered_text[:200]),
    )

    citations_path = f"{out_path}.citations.json"
    citations_payload = _write_citation_sidecar(drafts, pms, citations_path)
    _append_log(node_log, "emit_citation_sidecar", drafts, citations_payload)

    sanitize_report = sanitize_gate(rendered_text, pms)
    if not sanitize_report.ok:
        raise ValueError(f"Sanitize gate failed: {sanitize_report.violations!r}")
    _append_log(node_log, "sanitize_gate", rendered_text[:200], sanitize_report)

    validation_report = validate_public_artifact(
        out_path, citations_path, profile=active_profile
    )
    if not validation_report.ok:
        raise ValueError(f"Public artifact validation failed: {validation_report.errors!r}")
    _append_log(node_log, "validate_public_artifact", citations_payload, validation_report)

    planspec_path = save_planspec(out_path, planspec)
    rendered_drafts_path = save_drafts(out_path, drafts)
    pms_path = f"{out_path}.pms.json"
    _ = _save_pms_snapshot(pms, pms_path)
    return RunResult(
        artifact_path=out_path,
        citations_path=citations_path,
        node_log=node_log,
        planspec_path=planspec_path,
        drafts_path=rendered_drafts_path,
        pms_path=pms_path,
    )


def _bundle_base_for_drafts(drafts_path: str) -> str:
    suffix = ".drafts.json"
    return drafts_path[: -len(suffix)] if drafts_path.endswith(suffix) else drafts_path


def _source_spans(text: str, quoted: tuple[str, ...]) -> tuple[tuple[int, int], ...]:
    """Return merged spans that came byte-for-byte from corpus evidence."""
    matches: list[tuple[int, int]] = []
    for source in quoted:
        start = text.find(source)
        while start >= 0:
            matches.append((start, start + len(source)))
            start = text.find(source, start + 1)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(matches):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return tuple(merged)


def _correct_proposal_drafts(
    drafts: list[SectionDraft],
    evidence: list[EvidenceUnit],
    *,
    label: str,
    project: str,
) -> list[SectionDraft]:
    """Correct only generated body spans immediately before rendering.

    Exact evidence facts and provenance excerpts remain untouched, including
    non-public evidence that the sanitize gate must still recognize.
    """
    try:
        from automation import term_correction, term_correction_log, term_glossary
    except Exception as failure:  # Optional correction must not block rendering.
        print(
            f"TERM-CORRECTION-SKIP document=proposal reason={type(failure).__name__}",
            file=sys.stderr,
        )
        return drafts

    try:
        glossary = term_glossary.glossary_for("proposal", project)
        if not glossary:
            return drafts
        quoted = tuple(
            dict.fromkeys(
                text
                for unit in evidence
                for text in (unit.fact, *(item.verbatim for item in unit.provenances))
                if text
            )
        )
        corrected: list[SectionDraft] = []
        corrections: list[term_correction.Correction] = []
        for draft in drafts:
            cursor = 0
            pieces: list[str] = []
            for start, end in _source_spans(draft.body, quoted):
                fixed, found = term_correction.apply(draft.body[cursor:start], glossary)
                pieces.extend((fixed, draft.body[start:end]))
                corrections.extend(found)
                cursor = end
            fixed, found = term_correction.apply(draft.body[cursor:], glossary)
            pieces.append(fixed)
            corrections.extend(found)
            corrected.append(
                cast(SectionDraft, draft.model_copy(update={"body": "".join(pieces)}))
            )
    except Exception as failure:  # Correction is fail-soft and atomic.
        print(
            f"TERM-CORRECTION-SKIP document=proposal reason={type(failure).__name__}",
            file=sys.stderr,
        )
        return drafts

    try:
        _ = term_correction_log.record(
            corrections,
            document="proposal",
            label=label,
            project=project,
            stage="generated-body",
        )
    except Exception as failure:  # Audit failure cannot block the document.
        print(f"{term_correction_log.MARKER} {type(failure).__name__}", file=sys.stderr)
    return corrected


def _standard_spellings(
    drafts: list[SectionDraft],
    planspec: PlanSpec,
    evidence: list[EvidenceUnit],
) -> tuple[list[SectionDraft], PlanSpec]:
    """Unify loanword spellings in generated prose and the KPI table, never in evidence.

    Evidence spans stay byte-for-byte so the sanitize gate still recognizes a quoted
    non-public fact.
    """
    quoted = tuple(
        dict.fromkeys(
            text
            for unit in evidence
            for text in (unit.fact, *(item.verbatim for item in unit.provenances))
            if text
        )
    )
    normalized: list[SectionDraft] = []
    for draft in drafts:
        cursor = 0
        pieces: list[str] = []
        for start, end in _source_spans(draft.body, quoted):
            pieces.extend((normalize_spellings(draft.body[cursor:start]), draft.body[start:end]))
            cursor = end
        pieces.append(normalize_spellings(draft.body[cursor:]))
        normalized.append(cast(SectionDraft, draft.model_copy(update={"body": "".join(pieces)})))
    kpis = [
        kpi.model_copy(
            update={
                field: normalize_spellings(getattr(kpi, field))
                for field in ("name", "method", "env", "rationale")
            }
        )
        for kpi in planspec.kpis
    ]
    return normalized, cast(
        PlanSpec,
        planspec.model_copy(
            update={
                "kpis": kpis,
                "title": normalize_spellings(planspec.title),
                "objectives": [normalize_spellings(item) for item in planspec.objectives],
                "keywords": [normalize_spellings(item) for item in planspec.keywords],
            }
        ),
    )


def _write_rubric_findings(
    out_path: str,
    drafts: list[SectionDraft],
    planspec: PlanSpec,
    cover: Mapping[str, str],
) -> None:
    findings = check_rubric(drafts, planspec.kpis, cover)
    _write_json(
        Path(f"{out_path}.quality.json"),
        {
            "findings": [
                {
                    "code": finding.code,
                    "criterion": finding.criterion,
                    "section_id": finding.section_id,
                    "detail": finding.detail,
                }
                for finding in findings
            ]
        },
    )
    print(
        json.dumps(
            {
                "event": "rubric_findings",
                "count": len(findings),
                "codes": sorted({finding.code for finding in findings}),
                "path": f"{out_path}.quality.json",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _append_log(node_log: list[NodeLog], node_name: str, inputs: object, outputs: object) -> None:
    node_log.append(
        NodeLog(
            node_name=node_name,
            inputs_hash=stable_id(repr(inputs)[:200]),
            outputs_hash=stable_id(repr(outputs)[:200]),
        )
    )


def _save_pms_snapshot(pms: ProposalMaterialStore, path: str) -> dict[str, object]:
    units = _all_units(pms)
    payload: dict[str, object] = {
        "units": [cast(dict[str, object], unit.model_dump(mode="json")) for unit in units],
        "ledger": {unit.unit_id: pms.get_citation_status(unit.unit_id).value for unit in units},
    }
    _write_json(Path(path), payload)
    return payload


def _style_lint_and_prepare(
    drafts: list[SectionDraft],
    profile: LayoutProfile,
) -> list[SectionDraft]:
    prepared: list[SectionDraft] = []
    for draft in drafts:
        report = lint(draft, profile=profile)
        current = compress(draft, report.budget) if report.char_count > report.budget else draft
        forbidden_report = check_forbidden_expressions(current.body, domain=KIMM_DOMAIN)
        if not forbidden_report.ok:
            print(
                json.dumps(
                    {
                        "event": "forbidden_expressions_found",
                        "section_id": current.section_id,
                        "count": len(forbidden_report.violations),
                        "expressions": [
                            violation.expression for violation in forbidden_report.violations
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        prepared.append(_with_section_marker(current))
    return prepared


def _without_section_marker(draft: SectionDraft) -> SectionDraft:
    """Drop the bare title line ``_with_section_marker`` put in front of the body.

    The marker lets draft-stage checks find each section; in the document the form's
    own numbered heading already names the section, so the marker printed a stray
    "배경·필요성" line under "1. 연구 배경 및 필요성" and glued "요약문" onto the
    summary's first sentence (2026-09-28 live render). Artifact validation accepts the
    form headings for these tokens, so nothing downstream needs the marker.
    """
    first, newline, rest = draft.body.partition("\n")
    if not newline or first.strip() != draft.title.strip() or not rest.strip():
        return draft
    return cast(SectionDraft, draft.model_copy(update={"body": rest.lstrip("\n")}))


def _with_section_marker(draft: SectionDraft) -> SectionDraft:
    if draft.title in draft.body:
        return draft
    return draft.model_copy(update={"body": f"{draft.title}\n{draft.body}"})


def _revise_or_keep(
    drafts: list[SectionDraft],
    planspec: PlanSpec,
    critic_report: CriticReport,
    pms: ProposalMaterialStore,
    llm: LLMClient,
    critic_llm: LLMClient | None,
) -> tuple[list[SectionDraft], dict[str, object]]:
    ir_content = planspec.model_dump(mode="json")
    if not critic_report.rejections:
        return drafts, ir_content

    for round_index in range(MAX_REVISE_ROUNDS):
        if critic_llm is not None:
            _log_critic_reviser_handoff(round_index + 1, critic_report)
        try:
            revised_drafts, revised_ir_content = revise(drafts, ir_content, critic_report, pms, llm)
        except AssertionError:
            break

        drafts = [_with_section_marker(draft) for draft in revised_drafts]
        ir_content = revised_ir_content

        if round_index >= MAX_REVISE_ROUNDS - 1 or critic_llm is None:
            break

        critic_report = critique(PlanSpec.model_validate(ir_content), drafts, pms, llm=critic_llm)
        if not critic_report.rejections:
            break

    if critic_llm is not None:
        try:
            drafts = _apply_parallel_reviewer_pass(drafts, planspec, pms, llm)
        except RuntimeError as exc:
            _log_parallel_reviser_event("parallel_reviewer_pass_failed", str(exc))

    return drafts, ir_content


REVIEWER_PASS_TIMEOUT_ENV = "KIMM_DOCBOT_REVIEWER_TIMEOUT_SECONDS"
REVIEWER_PASS_DEFAULT_TIMEOUT = 600.0


def _reviewer_pass_timeout() -> float:
    """Seconds the parallel reviewer pass may wait for its reviewers.

    Each live review is one full Hermes completion, itself bounded at 600 s by the
    Hermes client. The old fixed 60 s meant the pass could never finish live: on the
    2026-09-28 node run both reviewers timed out and their opinions were dropped.
    """
    raw = os.environ.get(REVIEWER_PASS_TIMEOUT_ENV, "").strip()
    try:
        value = float(raw) if raw else REVIEWER_PASS_DEFAULT_TIMEOUT
    except ValueError:
        return REVIEWER_PASS_DEFAULT_TIMEOUT
    return value if value > 0 else REVIEWER_PASS_DEFAULT_TIMEOUT


def _apply_parallel_reviewer_pass(
    drafts: list[SectionDraft],
    planspec: PlanSpec,
    pms: ProposalMaterialStore,
    llm: LLMClient,
) -> list[SectionDraft]:
    personas = [
        ReviewerPersona("reviser_A", "엄격한 기술 검토자", llm),
        ReviewerPersona("reviser_B", "독자 친화적 편집자", llm),
    ]
    opinions = run_parallel_reviewers(
        personas, drafts, planspec, pms, timeout=_reviewer_pass_timeout()
    )
    if not opinions:
        return drafts

    try:
        merge_plan = build_merge_plan(drafts, opinions, pms)
    except ValueError as exc:
        _log_parallel_reviser_event("parallel_reviewer_merge_rejected", str(exc))
        return drafts

    if not merge_plan.hunks:
        return drafts

    _log_parallel_reviser_transcript(merge_plan, planspec.title)
    return _apply_unattended_decisions(drafts, merge_plan)


def _apply_unattended_decisions(
    drafts: list[SectionDraft],
    plan: MergePlan,
) -> list[SectionDraft]:
    applied: dict[str, str] = {}
    for hunk in plan.hunks:
        if hunk.conflict:
            _log_parallel_reviser_event(
                "parallel_reviser_conflict_kept_base",
                f"locator={hunk.locator!r}, variants={len(hunk.variants)}",
            )
            continue

        if not hunk.variants:
            continue

        sec_match = _SECTION_LOCATOR_RE.match(hunk.locator)
        if sec_match is None:
            continue

        applied[sec_match.group("n")] = hunk.variants[0].text

    if not applied:
        return drafts

    return [
        draft.model_copy(update={"body": applied[draft.section_id]})
        if draft.section_id in applied
        else draft
        for draft in drafts
    ]


def _log_parallel_reviser_transcript(plan: MergePlan, plan_name: str) -> None:
    sections = tuple(
        TranscriptSection(
            locator=hunk.locator,
            opinions=tuple(
                SectionOpinionEntry(
                    reviewer_id=variant.reviewer_id,
                    opinion_text=variant.text,
                    chosen=not hunk.conflict and index == 0,
                )
                for index, variant in enumerate(hunk.variants)
            ),
            aggregated="base" if hunk.conflict else "first_variant",
            result_text=hunk.base_text if hunk.conflict else hunk.variants[0].text,
            unresolved=hunk.conflict,
        )
        for hunk in plan.hunks
        if hunk.variants
    )
    transcript = RevisionTranscript(sections=sections, plan_name=plan_name)
    _log_parallel_reviser_event(
        "parallel_reviser_transcript",
        json.dumps(transcript.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
    )


def _log_parallel_reviser_event(event: str, detail: str = "") -> None:
    print(json.dumps({"detail": detail, "event": event}, ensure_ascii=False, sort_keys=True))


def _log_critic_reviser_handoff(round_number: int, critic_report: CriticReport) -> None:
    payload = {
        "event": "critic_reviser_handoff",
        "rejection_cells": [rejection.cell for rejection in critic_report.rejections],
        "rejections": [rejection.model_dump(mode="json") for rejection in critic_report.rejections],
        "round": round_number,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _check_section_requirements(drafts: list[SectionDraft]) -> list[dict[str, str]]:
    """Check each section draft contains its required sub-elements (warn-only)."""
    missing: list[dict[str, str]] = []
    for draft in sorted(drafts, key=lambda item: int(item.section_id)):
        requirements = KIMM_DOMAIN.section_requirements.get(draft.section_id, [])
        for req in requirements:
            if req not in draft.body:
                missing.append({"section_id": draft.section_id, "missing": req})
    return missing


def _render(
    out_path: str,
    planspec: PlanSpec,
    drafts: list[SectionDraft],
    pms: ProposalMaterialStore | None = None,
    *,
    context: RenderContext | None = None,
) -> str:
    entries = unpack(str(SEED_HWPX))
    entries = replace_entry(
        entries,
        HEADER_ENTRY,
        install_form_typography(entries[HEADER_ENTRY]),
    )
    anchor_map = load_anchor_map()
    section_xml = restyle_seed_form_runs(entries[SECTION_ENTRY])
    section_xml, anchor_map = remove_seed_guidance(
        section_xml,
        anchor_map,
        {draft.section_id: draft.body for draft in drafts},
    )
    section_xml = fill_seed_cover(
        section_xml,
        anchor_map,
        planspec,
        dict(context.cover_overrides) if context is not None else None,
    )

    if context is not None and context.tables_path is not None:
        for kind, data in reversed(_load_comparison_tables(context.tables_path)):
            section_xml = insert_comparison_table(
                section_xml,
                data,
                kind=kind,
                anchor_map=anchor_map,
            )
    section_xml = write_kpi_table(section_xml, planspec.kpis, anchor_map)
    gantt_years = (
        _load_gantt_years(context.tables_path)
        if context is not None and context.tables_path is not None
        else None
    )
    if gantt_years is not None:
        total_months = planspec.total_months
        expected_years = -(-total_months // GANTT_MONTHS) if total_months else len(gantt_years)
        if len(gantt_years) != expected_years:
            raise ValueError(
                f"추진 내용 표가 {len(gantt_years)}개 연차를 실었지만 계획은 "
                f"{total_months}개월({expected_years}개 연차)입니다"
            )
        section_xml = write_gantt_years(section_xml, gantt_years, anchor_map)
    else:
        section_xml = write_gantt(section_xml, _gantt_rows(planspec), anchor_map)

    if pms is not None:
        # 근거 추적성은 본문이 아니라 아티팩트 옆 사이드카 md 로 관리한다
        # (소유자 지시 2026-08-28) — 심사 문서에 내부 품질 지표를 싣지 않는다.
        evidence_graph = build_evidence_graph(pms, drafts)
        traceability_path = Path(f"{out_path}.traceability.md")
        traceability_path.write_text(traceability_markdown(evidence_graph), encoding="utf-8")
        print(
            json.dumps(
                {
                    "event": "coverage_score",
                    "score": evidence_graph.coverage_score,
                    "traceability": str(traceability_path),
                }
            )
        )

    image_specs: dict[str, ImageSpec] | None = None
    if context is not None and context.figures_path is not None:
        if context.images_dir is None:
            raise FigureDensityError("IMAGES_DIR_MISSING: --figures requires --images")
        expected_band_count = sum(context.profile.figure_targets.values())
        figures = load_figure_specs(
            context.figures_path,
            expected_band_count=expected_band_count,
        )
        bands, number_by_id = build_layout_bands(
            {draft.section_id: draft.body for draft in drafts},
            figures,
            expected_band_count=expected_band_count,
        )
        _ = validate_layout_bands(bands)
        bands_by_section = {
            section_id: tuple(band for band in bands if band.section_id == section_id)
            for section_id in {band.section_id for band in bands}
        }
        for section_id in sorted(bands_by_section, key=int, reverse=True):
            section_xml = render_layout_bands(
                section_xml,
                body_anchor_slot(section_id, anchor_map),
                bands_by_section[section_id],
                number_by_id,
                anchor_map,
            )
        image_specs = {}
        for figure in sorted(figures, key=lambda item: number_by_id[item.figure_id]):
            number = number_by_id[figure.figure_id]
            png_path = context.images_dir / f"{figure.figure_id}.png"
            if not png_path.is_file():
                raise FigureDensityError(
                    f"MISSING_FIGURE_IMAGE: {figure.figure_id}: {png_path}"
                )
            image_specs[f"image{number}"] = ImageSpec(
                path=png_path,
                caption=figure.caption,
            )
    else:
        for draft in sorted(drafts, key=lambda item: int(item.section_id), reverse=True):
            section_xml = render_body_paragraphs(
                section_xml,
                body_anchor_slot(draft.section_id, anchor_map),
                draft.body,
                anchor_map,
            )

    section_xml = trim_trailing_blank_paragraphs(section_xml, anchor_map)
    section_xml = apply_compact_table_runs(section_xml)
    section_xml = fit_tables(section_xml, skip_table_ids=(gantt_table_id(anchor_map),))
    entries = replace_entry(entries, SECTION_ENTRY, section_xml)
    entries = sync_prv_text(entries[SECTION_ENTRY], entries, section_xml)
    if image_specs is None:
        repack(entries, out_path)
    else:
        pre_embed = Path(f"{out_path}.bands.tmp")
        try:
            repack(entries, str(pre_embed))
            _ = embed_images(
                pre_embed,
                out_path,
                image_specs,
                max_display_width=(
                    context.figure_width_hwp if context is not None else MAX_DISPLAY_WIDTH
                ),
            )
        finally:
            pre_embed.unlink(missing_ok=True)
    return text_extract(out_path)


def render_candidate_artifact(
    out_path: str,
    planspec: PlanSpec,
    drafts: list[SectionDraft],
    *,
    context: RenderContext,
) -> str:
    """Render one convergence candidate through the canonical HWPX path."""
    return _render(out_path, planspec, drafts, context=context)


def _load_comparison_tables(path: Path) -> list[tuple[ComparisonKind, TableData]]:
    payload = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(payload, list):
        raise ValueError("tables manifest must contain a list")
    result: list[tuple[ComparisonKind, TableData]] = []
    for raw_item in cast(list[object], payload):
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[object, object], raw_item)
        kind = item.get("kind")
        if kind not in {"prior-research", "tech-gap"}:
            continue
        header = item.get("header")
        rows = item.get("rows")
        if (
            not isinstance(header, list)
            or len(header) != 3
            or not all(isinstance(value, str) for value in header)
            or not isinstance(rows, list)
            or not rows
            or not all(
                isinstance(row, list)
                and len(row) == 3
                and all(isinstance(value, str) for value in row)
                for row in rows
            )
        ):
            raise ValueError(f"invalid comparison table: {kind}")
        typed_header = cast(list[str], header)
        typed_rows = cast(list[list[str]], rows)
        result.append(
            (
                cast(ComparisonKind, kind),
                TableData(
                    (typed_header[0], typed_header[1], typed_header[2]),
                    tuple((row[0], row[1], row[2]) for row in typed_rows),
                ),
            )
        )
    return result


def _load_gantt_years(path: Path) -> list[tuple[int, list[GanttRow]]] | None:
    """tables.json 의 gantt 표를 연차별 행으로 해석한다 — 행은 [연차, 꼭지, 시작월, 종료월].

    월은 연차 안의 상대 월(1..12)이다. 스킬 쪽이 corpus 근거로 저작한 일정을 그대로
    싣는 채널이므로, 형식 오류는 조용한 폴백이 아니라 렌더 중단이다 — 저작된 일정이
    버려진 채 한 행짜리 표가 나가는 쪽이 더 나쁜 실패다.
    """
    payload = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(payload, list):
        raise ValueError("tables manifest must contain a list")
    entries = [
        cast(dict[object, object], item)
        for item in cast(list[object], payload)
        if isinstance(item, dict) and cast(dict[object, object], item).get("kind") == "gantt"
    ]
    if not entries:
        return None
    if len(entries) > 1:
        raise ValueError("tables manifest carries more than one gantt table")
    raw_rows = entries[0].get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("gantt table has no rows")
    rows_by_year: dict[int, list[GanttRow]] = {}
    for raw_row in cast(list[object], raw_rows):
        if not isinstance(raw_row, list) or len(cast(list[object], raw_row)) != 4:
            raise ValueError(f"gantt row must be [연차, 꼭지, 시작월, 종료월]: {raw_row!r}")
        year_text, label_text, start_text, end_text = cast(list[object], raw_row)
        try:
            year = int(str(year_text))
            start = int(str(start_text))
            end = int(str(end_text))
        except ValueError as error:
            raise ValueError(f"gantt row has non-numeric fields: {raw_row!r}") from error
        label = str(label_text).strip()
        if not label:
            raise ValueError(f"gantt row has an empty activity label: {raw_row!r}")
        if not 1 <= start <= end <= GANTT_MONTHS:
            raise ValueError(
                f"months must be in 1..{GANTT_MONTHS} and start <= end: {raw_row!r}"
            )
        rows_by_year.setdefault(year, []).append(
            GanttRow(label, frozenset(range(start, end + 1)))
        )
    years = sorted(rows_by_year.items())
    validate_gantt_years(years)
    return [(year, rows) for year, rows in years]


def _write_citation_sidecar(
    drafts: list[SectionDraft],
    pms: ProposalMaterialStore,
    citations_path: str,
) -> dict[str, list[dict[str, str]]]:
    claims: list[dict[str, str]] = []
    for draft in sorted(drafts, key=lambda item: int(item.section_id)):
        for claim in draft.claims:
            for source_id in sorted(claim.source_ids):
                claims.append(
                    {
                        "source_id": source_id,
                        "status": pms.get_citation_status(source_id).value,
                        "claim_text": claim.text,
                    }
                )
    payload = {"claims": claims}
    _write_json(Path(citations_path), payload)
    return payload


def _all_units(pms: ProposalMaterialStore) -> list[EvidenceUnit]:
    by_bucket = cast(Mapping[str, list[EvidenceUnit]], getattr(pms, "_by_bucket"))
    units_by_id = {
        unit.unit_id: unit
        for bucket in sorted(by_bucket)
        for unit in sorted(by_bucket[bucket], key=lambda item: item.unit_id)
    }
    return [units_by_id[unit_id] for unit_id in sorted(units_by_id)]


def _pms_unit_ids(pms: ProposalMaterialStore) -> list[str]:
    return [unit.unit_id for unit in _all_units(pms)]


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "DraftResult",
    "InsufficientEvidenceError",
    "NODE_NAMES",
    "RenderContext",
    "VALID_PROFILES",
    "draft",
    "render",
    "render_candidate_artifact",
    "run",
]
