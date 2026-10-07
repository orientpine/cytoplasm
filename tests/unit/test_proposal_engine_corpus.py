"""Corpus normalization and project-local work-package grounding."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from skills.proposal.engine.contract.planner import plan
from skills.proposal.engine.converter.ingest import ingest_dir
from skills.proposal.engine.converter.materialize import materialize
from skills.proposal.engine.converter.normalize import normalize
from skills.proposal.engine.converter.pms import ProposalMaterialStore


@pytest.mark.parametrize("value", ["2.13", "v1.9.1", "3.5%"])
def test_preserves_numeric_fact_when_corpus_contains_decimal(
    tmp_path: Path, value: str,
) -> None:
    # Given a real corpus file with a decimal inside one sentence.
    fact = f"KPI env: TensorFlow {value} 학습 서버로 설정한다"
    _ = (tmp_path / "source.md").write_text(f"{fact}. 다음 문장。마지막 문장\n", encoding="utf-8")

    # When corpus bytes pass through ingestion, normalization and materialization.
    units = materialize([normalize(raw) for raw in ingest_dir(str(tmp_path))])

    # Then only sentence punctuation splits facts; numeric tokens stay intact.
    assert {unit.fact for unit in units} == {fact, "다음 문장", "마지막 문장"}
    numeric_unit = next(unit for unit in units if unit.fact == fact)
    assert numeric_unit.provenances[0].verbatim == fact


@dataclass(frozen=True, slots=True)
class EmptyPhrasing:
    """External phrasing is irrelevant to deterministic package grounding."""

    def complete(self, role: str, prompt: str) -> str:
        assert role == "planner"
        assert prompt
        return "{}"


@pytest.mark.parametrize("separate_files", [False, True])
def test_extracts_project_local_phases_when_other_project_has_collaboration(
    tmp_path: Path, separate_files: bool,
) -> None:
    # Given unrelated collaboration before a four-phase schedule, in one or two files.
    unrelated = "**주요사업: 다른 합성 과제**\n협력; lead: **다른 연구팀**\n"
    target = (
        "**주요사업: 대상 합성 과제**\n"
        "연구목표: 센서 평가\n"
        "KPI: 평가율; baseline: 20; target: 80; weight: 100\n"
        "협력; lead: **대상 연구팀**\n"
        "일정은 1~36개월이며, 1~9개월 **준비**, 10~18개월 *구현*, "
        "19~27개월 __평가__, 28~36개월 검증으로 구성한다.\n"
    )
    if separate_files:
        _ = (tmp_path / "other.md").write_text(unrelated, encoding="utf-8")
        _ = (tmp_path / "target.md").write_text(target, encoding="utf-8")
    else:
        _ = (tmp_path / "combined.md").write_text(unrelated + target, encoding="utf-8")
    units = materialize([normalize(raw) for raw in ingest_dir(str(tmp_path))])

    # When the planner builds grounded packages rather than LLM-generated packages.
    result = plan(ProposalMaterialStore(units), EmptyPhrasing())

    # Then phases have local leads and clean, per-phase titles/deliverables.
    assert [(wp.title, wp.months, wp.lead, wp.deliverables) for wp in result.work_packages] == [
        (title, 9, "대상 연구팀", [title]) for title in ("준비", "구현", "평가", "검증")
    ]
    assert len({wp.wp_id for wp in result.work_packages}) == 4
    assert [(wp.start_month, wp.end_month) for wp in result.work_packages] == [
        (1, 9), (10, 18), (19, 27), (28, 36),
    ]


def test_keeps_lead_unassigned_when_only_other_project_has_lead(tmp_path: Path) -> None:
    # Given one source containing a named but unrelated project before our schedule.
    _ = (tmp_path / "combined.md").write_text(
        "**주요사업: 다른 합성 과제**\n협력; lead: **다른 연구팀**\n"
        "**주요사업: 대상 합성 과제**\n"
        "KPI: 평가율; baseline: 20; target: 80; weight: 100\n"
        "일정; title: **준비**; months: 6; deliverables: **준비서**\n",
        encoding="utf-8",
    )
    units = materialize([normalize(raw) for raw in ingest_dir(str(tmp_path))])

    # When the schedule has no grounded owner within its project.
    result = plan(ProposalMaterialStore(units), EmptyPhrasing())

    # Then no other project's owner or heading is invented as its lead.
    package, = result.work_packages
    assert (package.title, package.lead, package.deliverables) == (
        "준비", "public evidence", ["준비서"],
    )


def test_prefers_explicit_lead_when_package_provides_it(tmp_path: Path) -> None:
    # Given structured evidence whose explicit lead differs from collaboration.
    _ = (tmp_path / "source.md").write_text(
        "KPI: 평가율; baseline: 20; target: 80; weight: 100\n"
        "협력; lead: 다른 연구팀\n"
        "일정; title: **준비**; months: 6; lead: **직접 담당팀**; deliverables: *준비서*; "
        "start_month: 5; end_month: 10\n",
        encoding="utf-8",
    )
    units = materialize([normalize(raw) for raw in ingest_dir(str(tmp_path))])

    # When the planner resolves ownership.
    result = plan(ProposalMaterialStore(units), EmptyPhrasing())

    # Then the explicit lead wins and formatting is not data.
    package, = result.work_packages
    assert (package.title, package.lead, package.deliverables) == (
        "준비", "직접 담당팀", ["준비서"],
    )
    assert (package.start_month, package.end_month) == (5, 10)
