"""Structured schedule spans, project horizon and actual HWPX month cells."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from skills.proposal.engine.contracts import KPI, PlanSpec, TraceabilityMatrix, WorkPackage
from skills.proposal.engine.contracts.layout_profile import get_layout_profile
from skills.proposal.engine.hwpx.table_writer import GanttRow
from skills.proposal.engine.pipeline.orchestrator import (
    RenderContext, _gantt_rows, _plan_phases, render_candidate_artifact,
)


def _plan(packages: list[WorkPackage]) -> PlanSpec:
    return PlanSpec(
        title="합성 일정", trl_start=3, trl_end=6, objectives=[], keywords=[],
        kpis=[KPI("평가율", "%", "20", "80", 100, "측정", "서버", "근거")],
        work_packages=packages, page_budget={}, traceability=TraceabilityMatrix([]),
    )


@pytest.mark.parametrize(("spans", "expected"), [
    ([(1, 12), (1, 12)], 12),
    ([(1, 3), (16, 18)], 18),
])
def test_cover_period_uses_schedule_horizon(
    spans: list[tuple[int, int]], expected: int,
) -> None:
    from skills.proposal.engine.hwpx.seed_fill import cover_values_from_planspec

    packages = [
        WorkPackage(
            wp_id=f"WP-{index}", title="phase", lead="team",
            months=end - start + 1, deliverables=[],
            start_month=start, end_month=end,
        )
        for index, (start, end) in enumerate(spans)
    ]
    assert cover_values_from_planspec(_plan(packages))["period"] == f"총 {expected}개월"


@pytest.mark.parametrize("explicit", [False, True])
def test_schedule_horizon_preserves_unscheduled_package_duration(explicit: bool) -> None:
    packages = [
        WorkPackage(
            wp_id="WP-1", title="first", lead="team", months=12, deliverables=[],
            start_month=1 if explicit else None, end_month=12 if explicit else None,
        ),
        WorkPackage(wp_id="WP-2", title="next", lead="team", months=3, deliverables=[]),
    ]
    specification = _plan(packages)
    assert specification.total_months == 15
    assert "total_months" not in specification.model_dump(mode="json")


def test_authored_one_year_table_accepts_parallel_year_long_packages(tmp_path: Path) -> None:
    labels = tuple(f"parallel-{index}" for index in range(8))
    packages = [
        WorkPackage(
            wp_id=label, title=label, lead="team", months=12, deliverables=[],
            start_month=1, end_month=12,
        )
        for label in labels
    ]
    tables = tmp_path / "tables.json"
    tables.write_text(json.dumps([{
        "kind": "gantt", "rows": [[1, label, 1, 12] for label in labels],
    }]), encoding="utf-8")
    context = RenderContext(get_layout_profile(None), None, None, tables)
    output = tmp_path / "parallel.hwpx"
    render_candidate_artifact(str(output), _plan(packages), [], context=context)
    with ZipFile(output) as archive:
        root = ET.fromstring(archive.read("Contents/section0.xml"))
    ns = {"hp": "http://www.hancom.co.kr/hwpml/2011/paragraph"}
    rows = [
        ["".join(cell.itertext()).strip() for cell in row.findall("hp:tc", ns)]
        for row in root.findall(".//hp:tr", ns)
    ]
    for label in labels:
        row = next(cells for cells in rows if cells and cells[0] == label)
        assert sum(value == "■" for value in row[1:]) == 12


def test_prefers_structured_months_when_prose_disagrees() -> None:
    # Given explicit months that differ from both prose and sequential placement.
    package: WorkPackage = WorkPackage.model_validate({
        "wp_id": "WP-1", "title": "준비", "lead": "담당팀", "months": 3,
        "deliverables": ["1~2개월 구현, 3~4개월 검증"],
        "start_month": 5, "end_month": 7,
    })
    # When the schedule is planned.
    rows = _gantt_rows(_plan([package]))
    # Then the explicit span wins.
    assert rows == [GanttRow("준비", frozenset({5, 6, 7}))]


def test_keeps_overlapping_rows_when_packages_have_explicit_spans() -> None:
    # Given parallel packages and a phase beyond the first year.
    packages: list[WorkPackage] = [WorkPackage.model_validate({
        "wp_id": f"WP-{index}", "title": title, "lead": "담당팀",
        "months": end - start + 1, "deliverables": [],
        "start_month": start, "end_month": end,
    }) for index, (title, start, end) in enumerate([
        ("준비", 5, 7), ("평가", 6, 14), ("후속", 15, 18),
    ])]
    # When first-year rows are assembled.
    rows = _gantt_rows(_plan(packages))
    # Then both visible packages keep their actual starts and year-end clipping.
    assert rows == [
        GanttRow("준비", frozenset({5, 6, 7})),
        GanttRow("평가", frozenset(range(6, 13))),
    ]


def test_keeps_legacy_prose_when_month_fields_are_absent() -> None:
    # Given a persisted legacy package with a whole-project preamble.
    package = WorkPackage("WP-1", "기존 일정", "담당팀", 36, [
        "1~36개월이며, 1~9개월 준비, 10~18개월 구현, 19~36개월 검증으로 구성한다",
    ])
    # When old prose is consumed by the same schedule entry.
    rows = _gantt_rows(_plan([package]))
    # Then existing first-year phase placement remains supported.
    assert rows == [
        GanttRow("준비", frozenset(range(1, 10))),
        GanttRow("구현", frozenset({10, 11, 12})),
    ]


def test_keeps_sequential_fallback_when_no_spans_exist() -> None:
    # Given two duration-only legacy packages.
    first = WorkPackage("WP-1", "준비", "담당팀", 4, [])
    second = replace(first, wp_id="WP-2", title="평가", months=3)
    # When no structured or textual spans exist.
    rows = _gantt_rows(_plan([first, second]))
    # Then packages remain sequential.
    assert rows == [GanttRow("준비", frozenset(range(1, 5))), GanttRow("평가", frozenset({5, 6, 7}))]


@pytest.mark.parametrize("start,end", [(None, 4), (1, None), (0, 3), (5, 3), (2, 8)])
def test_rejects_invalid_span_when_loading_package(start: int | None, end: int | None) -> None:
    # Given a partial, invalid, or duration-inconsistent structured span.
    payload: dict[str, str | int | None | list[str]] = {
        "wp_id": "WP-1", "title": "준비", "lead": "담당팀", "months": 3,
        "deliverables": [], "start_month": start, "end_month": end,
    }
    # When the boundary parses it, then invalid schedules fail closed.
    with pytest.raises(ValueError):
        WorkPackage.model_validate(payload)


def test_roundtrips_month_fields_when_serializing_package() -> None:
    # Given a package from a persisted structured planspec.
    payload: dict[str, str | int | None | list[str]] = {
        "wp_id": "WP-1", "title": "준비", "lead": "담당팀", "months": 3,
        "deliverables": [], "start_month": 5, "end_month": 7,
    }
    # When it traverses the shared JSON codec.
    result: WorkPackage = WorkPackage.model_validate_json(WorkPackage.model_validate(payload).model_dump_json())
    # Then the machine-consumed month fields survive unchanged.
    assert result.model_dump() == payload


def test_omits_late_phase_when_only_second_year_is_scheduled() -> None:
    # Given an explicitly scheduled second-year phase.
    package: WorkPackage = WorkPackage.model_validate({
        "wp_id": "WP-1", "title": "후속", "lead": "담당팀", "months": 3,
        "deliverables": [], "start_month": 15, "end_month": 17,
    })
    # When phase rows are requested.
    rows = _plan_phases(package)
    # Then it does not silently become a sequential first-year phase.
    assert rows == []


def test_renders_actual_month_cells_when_package_has_structured_span(tmp_path: Path) -> None:
    # Given an explicit non-sequential span and the engine's real shipped seed.
    package: WorkPackage = WorkPackage.model_validate({
        "wp_id": "WP-1", "title": "준비", "lead": "담당팀", "months": 3,
        "deliverables": [], "start_month": 5, "end_month": 7,
    })
    output = tmp_path / "schedule.hwpx"
    context = RenderContext(get_layout_profile(None), None, None)
    # When the real public rendering entry writes an HWPX, without renderer mocks.
    _ = render_candidate_artifact(str(output), _plan([package]), [], context=context)
    # Then the actual XML cells, not an internal projection, mark months 5..7.
    with ZipFile(output) as archive:
        root = ET.fromstring(archive.read("Contents/section0.xml"))
    ns = {"hp": "http://www.hancom.co.kr/hwpml/2011/paragraph"}
    cells_by_row = [
        ["".join(cell.itertext()).strip() for cell in row.findall("hp:tc", ns)]
        for row in root.findall(".//hp:tr", ns)
    ]
    row = next(cells for cells in cells_by_row if cells and cells[0] == "준비")
    assert {month for month, value in enumerate(row[1:], 1) if value == "■"} == {5, 6, 7}
