"""Ground work packages in project-local schedule and collaboration evidence."""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from ..contracts import EvidenceUnit, WorkPackage

from .evidence_fields import (
    fact_data as _fact_data,
    int_field as _int_field,
    list_field as _list_field,
    regex_int as _regex_int,
    regex_text as _regex_text,
    text_field as _text_field,
    title_fragment as _title_fragment,
)

MONTH_RE: Final = re.compile(r"(?P<months>\d+)\s*(?:개월|months?|mo\b)", re.IGNORECASE)
WP_ID_RE: Final = re.compile(r"\bWP[-_ ]?\d+\b", re.IGNORECASE)
PROJECT_RE: Final = re.compile(r"^(?:주요사업|과제명|과제|project)\s*[:：]", re.IGNORECASE)
PHASE_RE: Final = re.compile(
    r"(?P<start>\d+)\s*[-–~]\s*(?P<end>\d+)\s*개월\s*(?P<label>[^,;\n]+)"
)
PHASE_TAIL_RE: Final = re.compile(r"(?:으?로\s*구성한다|으?로\s*나눈다|이다)[.。]?\s*$")
EMPHASIS_RE: Final = re.compile(r"(?<!\w)(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1(?!\w)")
LEAD_KEYS: Final = ("lead", "owner", "담당", "주관", "책임", "기관")


class InsufficientEvidenceError(ValueError):
    """Evidence cannot ground a required part of the plan."""


def plain_text(text: str) -> str:
    """Remove paired Markdown emphasis without damaging identifiers' underscores."""
    return EMPHASIS_RE.sub(r"\2", text).strip()


def _project_scopes(units: Sequence[EvidenceUnit]) -> dict[str, frozenset[tuple[str, int]]]:
    """Resolve each provenance to its source and nearest preceding project boundary."""
    boundaries: dict[str, list[int]] = {}
    positions: dict[str, list[tuple[str, int]]] = {}
    for unit in units:
        is_boundary = PROJECT_RE.match(plain_text(unit.fact).lstrip("# ")) is not None
        for provenance in unit.provenances:
            _, separator, index = provenance.location.rpartition(":")
            if not separator or not index.isdecimal():
                continue
            position = int(index)
            positions.setdefault(unit.unit_id, []).append((provenance.source_id, position))
            if is_boundary:
                boundaries.setdefault(provenance.source_id, []).append(position)
    return {
        unit_id: frozenset(
            (source, max((p for p in boundaries.get(source, []) if p <= position), default=-1))
            for source, position in locations
        )
        for unit_id, locations in positions.items()
    }


def build_work_packages(
    schedule_units: Sequence[EvidenceUnit],
    collaboration_units: Sequence[EvidenceUnit],
    public_evidence: Sequence[EvidenceUnit],
) -> tuple[list[WorkPackage], list[EvidenceUnit]]:
    scopes = _project_scopes(public_evidence)
    packages: list[WorkPackage] = []
    grounded_units: list[EvidenceUnit] = []
    for unit in schedule_units:
        local = [
            candidate for candidate in collaboration_units
            if scopes.get(unit.unit_id, frozenset())
            & scopes.get(candidate.unit_id, frozenset())
        ]
        extracted = _packages_from_evidence(unit, local)
        if extracted:
            packages.extend(extracted)
            grounded_units.append(unit)
    if not packages:
        raise InsufficientEvidenceError("No public schedule evidence with a grounded duration")
    return packages, grounded_units


def _packages_from_evidence(
    unit: EvidenceUnit, collaboration: Sequence[EvidenceUnit],
) -> list[WorkPackage]:
    text = plain_text(unit.fact)
    data = _fact_data(text)
    explicit_lead = _text_field(data, LEAD_KEYS)
    leads = {
        plain_text(lead) for candidate in collaboration
        if (lead := _text_field(_fact_data(plain_text(candidate.fact)), LEAD_KEYS))
    }
    lead = explicit_lead or (next(iter(leads)) if len(leads) == 1 else "public evidence")
    wp_id = _text_field(data, ("wp_id", "id", "작업패키지")) or _regex_text(WP_ID_RE, text)
    base_id = wp_id or f"WP-{unit.unit_id}"
    matches = list(PHASE_RE.finditer(text))
    packages: list[WorkPackage] = []
    for index, match in enumerate(matches, 1):
        start, end = int(match["start"]), int(match["end"])
        if start < 1 or end < start:
            raise InsufficientEvidenceError(f"Invalid phase months: {start}..{end}")
        # A span enclosing all other spans is a project preamble, not a phase.
        if len(matches) > 1 and all(
            start <= int(other["start"]) and int(other["end"]) <= end
            for other in matches
        ):
            continue
        title = PHASE_TAIL_RE.sub("", match["label"]).strip()
        if not title:
            raise InsufficientEvidenceError(f"Missing phase label: {unit.unit_id}")
        packages.append(WorkPackage(
            wp_id=f"{base_id}-{index}", title=title, lead=lead,
            months=end - start + 1, deliverables=[title], start_month=start, end_month=end,
        ))
    if packages:
        return packages
    months = _int_field(data, ("months", "month", "duration", "기간", "개월"))
    if months is None:
        months = _regex_int(MONTH_RE, text, "months")
    if months is None:
        return []
    title = _text_field(data, ("title", "name", "제목")) or _title_fragment(text)
    deliverables = _list_field(data, ("deliverables", "deliverable", "산출물", "결과물"))
    return [WorkPackage(
        wp_id=base_id, title=title, lead=lead, months=months,
        deliverables=deliverables or [text],
        start_month=_month_field(_text_field(data, ("start_month", "착수월"))),
        end_month=_month_field(_text_field(data, ("end_month", "종료월"))),
    )]


def _month_field(value: str | None) -> int | None:
    if value is None:
        return None
    if not value.isdecimal():
        raise InsufficientEvidenceError(f"Invalid structured month: {value!r}")
    return int(value)
