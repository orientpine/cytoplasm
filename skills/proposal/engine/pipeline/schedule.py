"""Resolve first-year Gantt rows from structured or legacy work packages."""
from __future__ import annotations

import re
from typing import Final, assert_never

from ..contracts.models import PlanSpec, WorkPackage
from ..hwpx.table_writer import GANTT_MONTHS, GanttRow

_PLAN_PHASE_RE: Final = re.compile(
    r"(?P<start>\d{1,2})\s*[-–~]\s*(?P<end>\d{1,2})\s*개월\s*(?P<label>[^,;]+)"
)
_PHASE_TAIL_RE: Final = re.compile(r"(?:로\s*구성한다|으?로\s*나눈다|이다)\s*$")


def _plan_phases(source: WorkPackage | str) -> list[GanttRow]:
    """Prefer explicit months; keep prose parsing for persisted legacy plans."""
    match source:
        case WorkPackage():
            if source.start_month is not None and source.end_month is not None:
                if source.start_month > GANTT_MONTHS:
                    return []
                return [GanttRow(source.title, frozenset(range(
                    source.start_month, min(source.end_month, GANTT_MONTHS) + 1,
                )))]
            return _plan_phases(source.title) or _plan_phases(" ".join(source.deliverables))
        case str():
            text = source
        case unreachable:
            assert_never(unreachable)
    matches = list(_PLAN_PHASE_RE.finditer(text))
    if len(matches) < 2:
        return []
    total = max(int(match.group("end")) for match in matches)
    phases: list[GanttRow] = []
    for match in matches:
        start, end = int(match.group("start")), int(match.group("end"))
        if start == 1 and end == total:
            continue
        label = _PHASE_TAIL_RE.sub("", match.group("label").strip()).strip()
        if not label or start > GANTT_MONTHS:
            continue
        phases.append(GanttRow(label, frozenset(range(start, min(end, GANTT_MONTHS) + 1))))
    return phases


def gantt_rows(planspec: PlanSpec) -> list[GanttRow]:
    """Keep absolute spans, including overlaps; sequence only unscheduled packages."""
    packages = sorted(planspec.work_packages, key=lambda item: item.wp_id)
    rows: list[GanttRow] = []
    elapsed = 0
    for package in packages:
        phases = _plan_phases(package)
        if package.end_month is not None:
            elapsed = max(elapsed, package.end_month)
            rows.extend(phases)
        elif phases:
            elapsed += package.months
            rows.extend(phases)
        else:
            start = elapsed + 1
            end = min(GANTT_MONTHS, elapsed + package.months)
            elapsed += package.months
            if start <= GANTT_MONTHS:
                rows.append(GanttRow(package.title, frozenset(range(start, end + 1))))
    if not rows:
        rows.append(GanttRow("추진 일정 미정", frozenset()))
    return rows
