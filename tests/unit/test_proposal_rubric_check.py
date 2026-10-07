"""제안서 평가 감점 사유를 결정적으로 찾는 검사와 KPI 표 머리행 회귀.

2026-09-30 소유자 평가()가 짚은 감점 — 한 지표의 기준값 두 개, 같은 목표·일정의
반복, 반복 횟수·산정식 없는 측정 절차, 용어 혼용, 내용과 맞지 않는 성과지표 표 열 제목 —
을 각각 한 가지 행동으로 고정한다.
"""

from __future__ import annotations

import re

from skills.proposal.engine.agents.rubric_check import check_rubric, normalize_spellings
from skills.proposal.engine.contracts import KPI, SectionDraft
from skills.proposal.engine.hwpx.anchor_map import DEFAULT_SEED_PATH, load_anchor_map
from skills.proposal.engine.hwpx.table_writer import (
    _anchored_table_id,
    _find_table_rows_by_id,
    write_kpi_table,
)
from skills.proposal.engine.hwpx.zip_surgery import unpack

_MAPE = KPI(
    name="버킷 궤적 추종 정확도(MAPE)",
    unit="%",
    baseline="6%",
    target="3%",
    weight=60,
    method="시나리오 3개 × 각 30회 반복 시험, MAPE = 평균(|실측-목표|÷목표)×100",
    env="옥외 실증장",
    rationale="",
)
_SUCCESS = KPI(
    name="자율 굴착 작업 성공률",
    unit="%",
    baseline="70%",
    target="95%",
    weight=40,
    method="지형 3종 × 각 20회 시행, 성공률 = 완주 사이클 수 ÷ 전체 시행 수 × 100",
    env="옥외 실증장",
    rationale="",
)


def _drafts(**bodies: str) -> list[SectionDraft]:
    return [
        SectionDraft(section_id=section_id, title=f"절 {section_id}", body=body, claims=[])
        for section_id, body in sorted(bodies.items())
    ]


def _clean_bodies() -> dict[str, str]:
    return {
        "0": "핵심 성과지표는 버킷 궤적 추종 정확도(MAPE) 6% → 3%, 자율 굴착 작업 성공률 70% → 95%다.",
        "1": "숙련도 의존이 작업 품질을 가른다.",
        "2": "버킷 궤적 추종 정확도(MAPE)는 6% → 3%로 둔다. 자율 굴착 작업 성공률은 70% → 95%다.",
        "3": "측정은 2-3절 성과지표의 절차를 따른다.",
        "4": "성과는 2-3절 성과지표로 판정한다.",
    }


def _codes(bodies: dict[str, str]) -> set[str]:
    return {finding.code for finding in check_rubric(_drafts(**bodies), [_MAPE, _SUCCESS])}


def test_a_clean_proposal_has_no_findings() -> None:
    # Given / When: 지표를 계획의 값으로 한 번씩만 쓰고 절차가 계획에 있는 초안.
    codes = _codes(_clean_bodies())

    # Then: 감점 사유가 하나도 없다.
    assert codes == set()


def test_a_second_baseline_for_the_same_kpi_is_named() -> None:
    # Given: 표의 6% 와 다른 원자료 값 6.46% 를 같은 지표에 붙인 배경 절.
    bodies = {**_clean_bodies(), "1": "MAPE 6.46%는 통제된 조건의 출발값이다."}

    # When / Then: 논리적 일관성 감점 사유로 잡힌다.
    assert "KPI_VALUE_DRIFT" in _codes(bodies)


def test_a_value_after_another_kpi_name_is_not_attributed_to_the_first() -> None:
    # Given: 여러 지표를 나열한 요약 줄 끝의 부수 지표(에너지 7%).
    bodies = {
        **_clean_bodies(),
        "0": "버킷 궤적 추종 정확도(MAPE) 6% → 3% / 자율 굴착 작업 성공률 70% → 95% / 에너지 7% 절감",
    }

    # When / Then: 7% 는 MAPE 의 두 번째 기준값이 아니다.
    assert "KPI_VALUE_DRIFT" not in _codes(bodies)


def test_a_kpi_without_trial_count_or_formula_is_named() -> None:
    # Given: 반복 횟수·산정식이 계획에도 본문에도 없는 지표.
    vague = KPI(
        name="자율 굴착 작업 성공률",
        unit="%",
        baseline="70%",
        target="95%",
        weight=40,
        method="시퀀스 완주 여부로 판정",
        env="옥외 실증장",
        rationale="",
    )

    # When: 검사한다.
    findings = check_rubric(_drafts(**_clean_bodies()), [_MAPE, vague])

    # Then: 구체성 감점 사유로 잡히고, 절차를 갖춘 지표는 잡히지 않는다.
    missing = [f for f in findings if f.code == "KPI_PROTOCOL_MISSING"]
    assert [f.detail.split(":")[0] for f in missing] == ["자율 굴착 작업 성공률"]


def test_a_target_restated_outside_the_goal_section_is_named() -> None:
    # Given: 기대효과 절이 목표값을 다시 적는다.
    bodies = {**_clean_bodies(), "4": "자율 굴착 작업 성공률 95%를 달성해 현장 적용을 앞당긴다."}

    # When / Then: 간결성 감점 사유로 잡힌다.
    assert "KPI_RESTATED" in _codes(bodies)


def test_a_schedule_repeated_inside_the_summary_is_named() -> None:
    # Given: 표지 칸과 요약 본문이 같은 월 구간을 싣는다.
    findings = check_rubric(
        _drafts(**{**_clean_bodies(), "0": "1~9개월 인프라를 구축한다."}),
        [_MAPE, _SUCCESS],
        {"annual_goal": "1~9개월 시뮬레이터·실증 인프라"},
    )

    # When / Then: 간결성 감점 사유로 잡힌다.
    assert "SCHEDULE_RESTATED" in {finding.code for finding in findings}


def test_a_non_standard_spelling_is_named_and_normalized() -> None:
    # Given: 외래어 비표준 표기가 섞인 본문.
    bodies = {**_clean_bodies(), "3": "버켓 끝단의 궤적을 기록한다."}

    # When / Then: 단어 적절성 감점 사유로 잡히고, 정규화는 표준 표기로 바꾼다.
    assert "TERM_VARIANT" in _codes(bodies)
    assert normalize_spellings("버켓 궤적") == "버킷 궤적"


def _kpi_table_rows(section: bytes) -> list[list[str]]:
    anchor_map = load_anchor_map()
    written = write_kpi_table(section, [_MAPE, _SUCCESS], anchor_map)
    rows = _find_table_rows_by_id(written, _anchored_table_id(anchor_map, "kpi.row.0.name"))
    return [
        re.findall(r"<hp:t>([^<]*)</hp:t>", written[start:end].decode("utf-8"))
        for start, end in rows
    ]


def test_the_kpi_table_header_names_what_its_cells_hold() -> None:
    # Given: 양식의 첫 표(구분 | 연차 | 목표)에 KPI 를 쓰는 렌더.
    section = unpack(str(DEFAULT_SEED_PATH))["Contents/section0.xml"]

    # When: KPI 표를 쓴다.
    header, first, *_ = _kpi_table_rows(section)

    # Then: 머리행이 성과지표·범위·측정 방법을 말하고, 근거 칸이 범위를 되풀이하지 않는다.
    assert header == ["성과지표", "현 수준 → 목표 (가중치)", "측정 방법 · 시험 환경 · 설정 근거"]
    assert first[1] == "6% → 3% (60%)"
    assert "대비 목표" not in first[2]
