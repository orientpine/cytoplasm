from __future__ import annotations

import json
from pathlib import Path

from ..contracts.collab_models import MergeDecision, MergePlan
from ..contracts.models import Claim, SectionDraft


def merge(
    *,
    bundle: str,
    opinions_dir: str,
    decisions_path: str,
    out_path: str,
) -> int:
    from ..agents.merge_decisions import resolve_decisions_from_file
    from ..agents.merge_engine import build_merge_plan
    from ..agents.opinion_parser import parse_opinion_dir
    from ..agents.transcript import (
        build_transcript,
        save_transcript_json,
        save_transcript_md,
    )
    from ..hwpx.validate import validate_public_artifact
    from .draft_bundle import (
        load_drafts,
        load_planspec,
        load_pms,
        save_drafts,
        save_planspec,
    )
    from .orchestrator import (
        _render,
        _save_pms_snapshot,
        _write_citation_sidecar,
    )

    _write_stdout(
        event="merge_start", bundle=bundle, opinions=opinions_dir, decisions=decisions_path
    )

    try:
        planspec = load_planspec(f"{bundle}.planspec.json")
        drafts = load_drafts(f"{bundle}.drafts.json")
        pms = load_pms(f"{bundle}.pms.json")
    except (OSError, ValueError) as exc:
        _write_stdout(event="merge_failed", step="load_bundle", error=str(exc))
        return 1

    _write_stdout(event="merge_bundle_loaded", sections=len(drafts))

    try:
        opinions = parse_opinion_dir(Path(opinions_dir))
    except (OSError, ValueError) as exc:
        _write_stdout(event="merge_failed", step="parse_opinions", error=str(exc))
        return 1

    _write_stdout(event="merge_opinions_parsed", count=len(opinions))

    try:
        plan = build_merge_plan(drafts, opinions, pms)
    except ValueError as exc:
        _write_stdout(event="merge_failed", step="build_plan", error=str(exc))
        return 1

    _write_stdout(event="merge_plan_built", hunks=len(plan.hunks))

    try:
        public_ids = frozenset(unit.unit_id for unit in pms.public_evidence())
        decisions = resolve_decisions_from_file(
            plan, decisions_path, public_source_ids=public_ids
        )
    except (OSError, ValueError) as exc:
        _write_stdout(event="merge_failed", step="resolve_decisions", error=str(exc))
        return 1

    merged_drafts = apply_merge_decisions_to_drafts(drafts, plan, decisions)
    transcript = build_transcript(opinions, plan, decisions, plan_name=planspec.title)
    transcript_base = str(Path(out_path).with_suffix("")) + ".transcript"
    save_transcript_json(transcript, f"{transcript_base}.json")
    save_transcript_md(transcript, f"{transcript_base}.md")
    _write_stdout(event="merge_transcript_saved", json=f"{transcript_base}.json")

    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        _ = _render(out_path, planspec, merged_drafts, pms)
    except (OSError, KeyError, ValueError) as exc:
        _write_stdout(event="merge_failed", step="render", error=str(exc))
        return 1

    citations_path = f"{out_path}.citations.json"
    _write_citation_sidecar(merged_drafts, pms, citations_path)
    merged_planspec_path = save_planspec(out_path, planspec)
    merged_drafts_path = save_drafts(out_path, merged_drafts)
    _save_pms_snapshot(pms, f"{out_path}.pms.json")
    _write_stdout(
        event="merge_bundle_saved",
        planspec=merged_planspec_path,
        drafts=merged_drafts_path,
        pms=f"{out_path}.pms.json",
    )

    try:
        validation = validate_public_artifact(out_path, citations_path)
    except (OSError, ValueError) as exc:
        _write_stdout(event="merge_failed", step="validate", error=str(exc))
        return 1
    if not validation.ok:
        _write_stdout(event="merge_citation_validation_failed", errors=list(validation.errors))
        return 1

    _write_stdout(
        event="merge_completed",
        out=out_path,
        citations=citations_path,
        transcript_json=f"{transcript_base}.json",
    )
    return 0


def apply_merge_decisions_to_drafts(
    drafts: list[SectionDraft],
    plan: MergePlan,
    decisions: list[MergeDecision],
) -> list[SectionDraft]:
    import re as _re

    decision_by_hunk = {decision.hunk_id: decision for decision in decisions}
    draft_map: dict[str, SectionDraft] = {draft.section_id: draft for draft in drafts}

    for hunk in plan.hunks:
        decision = decision_by_hunk.get(hunk.hunk_id)
        if decision is None:
            continue

        sec_match = _re.match(r"^sec(\d+)$", hunk.locator)
        claim_match = _re.match(r"^sec(\d+)\.claim(\d+)$", hunk.locator)

        if sec_match is not None:
            section_id = sec_match.group(1)
            draft = draft_map.get(section_id)
            if draft is not None:
                draft_map[section_id] = draft.model_copy(update={"body": decision.final_text})
            continue

        if claim_match is not None:
            section_id = claim_match.group(1)
            claim_idx = int(claim_match.group(2))
            draft = draft_map.get(section_id)
            if draft is None or claim_idx > len(draft.claims):
                continue
            source_ids = [
                source_id
                for claim in decision.final_claims
                for source_id in claim.source_ids
                if source_id != "_base_"
            ]
            new_claims = list(draft.claims)
            if claim_idx == len(new_claims):
                new_claims.append(Claim(text=decision.final_text, source_ids=source_ids))
            else:
                previous_claim = new_claims[claim_idx]
                new_claims[claim_idx] = Claim(
                    text=decision.final_text,
                    source_ids=source_ids or previous_claim.source_ids,
                )
            draft_map[section_id] = draft.model_copy(update={"claims": new_claims})

    return [draft_map.get(draft.section_id, draft) for draft in drafts]


def _write_stdout(**payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


__all__ = ["apply_merge_decisions_to_drafts", "merge"]
