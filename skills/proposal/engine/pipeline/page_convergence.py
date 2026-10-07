"""Pipeline adapter for the bounded HWPX page-convergence engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from ..contracts.layout_profile import get_layout_profile
from ..contracts.models import SectionDraft
from ..hwpx.image_embed import mm_to_hwp_units
from ..hwpx.page_convergence import (
    AutoPageMeasurer,
    CandidateState,
    ConvergenceBundle,
    ConvergenceResult,
    EstimateMeasurer,
    OptionalParagraph,
    PageMeasurer,
    SofficePdfMeasurer,
    converge_pages,
    read_hwpx_candidate_sections,
)
from .draft_bundle import load_drafts, load_planspec
from .orchestrator import RenderContext, render_candidate_artifact

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def converge_from_paths(
    *,
    drafts_path: str | Path,
    out_path: str | Path,
    profile: str = "30-page",
    images_dir: str | Path | None = None,
    figures_path: str | Path | None = None,
    measurer_name: str = "auto",
    target: int = 30,
) -> ConvergenceResult:
    """Load a draft bundle and preserve its closest rendered candidate at ``out``.

    Optional paragraph entries accept either a string or an object with ``text``
    (or ``body``), ``id``, ``priority``, and ``included`` fields. A bundle with no
    such markers intentionally degrades to the finite figure-width sequence.
    """
    source = Path(drafts_path)
    raw_payload = cast(object, json.loads(source.read_text(encoding="utf-8")))
    if isinstance(raw_payload, dict):
        payload = cast(dict[str, object], raw_payload)
        raw_sections = payload.get("sections")
    else:
        raw_sections = raw_payload
    if not isinstance(raw_sections, list):
        raise ValueError("draft bundle is missing required 'sections' field")
    section_items = cast(list[object], raw_sections)
    drafts = load_drafts(str(source))
    optionals: list[OptionalParagraph] = []
    initial: set[str] = set()
    for section_index, section_value in enumerate(section_items):
        if not isinstance(section_value, dict):
            continue
        raw_section = cast(dict[str, object], section_value)
        optionals_value = raw_section.get("optional_paragraphs", [])
        if not isinstance(optionals_value, list):
            raise ValueError("optional_paragraphs must be a list")
        for option_index, optional_value in enumerate(cast(list[object], optionals_value)):
            default_id = f"s{section_index}-optional-{option_index}"
            if isinstance(optional_value, str):
                item = OptionalParagraph(default_id, optional_value, section_index=section_index)
            elif isinstance(optional_value, dict):
                raw_optional = cast(dict[str, object], optional_value)
                text_value = raw_optional.get("text", raw_optional.get("body"))
                if not isinstance(text_value, str):
                    raise ValueError("optional paragraph text/body must be a string")
                identifier = raw_optional.get("id", default_id)
                priority = raw_optional.get("priority", 100)
                included = raw_optional.get("included", False)
                if not isinstance(identifier, str) or not isinstance(priority, int):
                    raise ValueError("optional paragraph id/priority must be string/integer")
                if not isinstance(included, bool):
                    raise ValueError("optional paragraph included must be boolean")
                item = OptionalParagraph(
                    identifier, text_value, priority, included, section_index
                )
            else:
                raise ValueError("optional paragraph entries must be strings or objects")
            optionals.append(item)
            if item.included:
                initial.add(item.paragraph_id)

    bundle_base = str(source)
    if bundle_base.endswith(".drafts.json"):
        bundle_base = bundle_base[: -len(".drafts.json")]
    planspec = load_planspec(f"{bundle_base}.planspec.json")
    active_profile = get_layout_profile(profile)
    output = Path(out_path)
    work_dir = output.parent / f".page-convergence-{output.stem}"

    def render_candidate(state: CandidateState, candidate: Path) -> None:
        candidate_drafts: list[SectionDraft] = []
        for index, draft in enumerate(drafts):
            additions = [
                item.text
                for item in sorted(optionals, key=lambda item: (item.priority, item.paragraph_id))
                if item.section_index == index and item.paragraph_id in state.enabled_optional_ids
            ]
            body = "\n\n".join((draft.body, *additions)) if additions else draft.body
            candidate_drafts.append(draft.model_copy(update={"body": body}))
        context = RenderContext(
            profile=active_profile,
            images_dir=Path(images_dir) if images_dir is not None else None,
            figures_path=Path(figures_path) if figures_path is not None else None,
            figure_width_hwp=mm_to_hwp_units(state.figure_width_mm),
        )
        _ = render_candidate_artifact(
            str(candidate), planspec, candidate_drafts, context=context
        )

    bundle = ConvergenceBundle(
        tuple(draft.body for draft in drafts),
        tuple(optionals),
        work_dir,
        render_candidate,
        read_hwpx_candidate_sections,
        frozenset(initial),
        output,
    )
    seed = PROJECT_ROOT / "resource" / "R&D 연구계획서 양식.hwpx"
    if measurer_name == "estimate":
        measurer: PageMeasurer = EstimateMeasurer()
    elif measurer_name == "soffice":
        measurer = SofficePdfMeasurer(seed)
    elif measurer_name == "auto":
        measurer = AutoPageMeasurer(SofficePdfMeasurer(seed))
    else:
        raise ValueError(f"unknown page measurer: {measurer_name}")
    return converge_pages(bundle, profile=active_profile, measurer=measurer, target=target)


__all__ = ["converge_from_paths"]
