"""Deterministically augment the todo-24 excavator replay bundle for full rendering."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import cast

from .proposal_ir import FIG_TOKEN_RE, FigureSpec, PROFILES, TableSpec, figures_to_json, tables_to_json

_SECTION_THEMES = {
    "0": "현장 지형 인식과 목표 지형 명세를 하나의 작업 계약으로 연결하고 안전 정지 조건을 우선 적용한다.",
    "1": "기존 자동화 장비와 달리 측량 갱신, 토공 계획, 굴착 제어, 결과 검증을 폐루프로 통합해 운전자 개입을 줄인다.",
    "2": "성과는 지형 오차, 작업 시간, 안전 개입, 에너지 사용량을 동일 시험 절차에서 반복 측정해 판정한다.",
    "3": "센서 융합, 토질 추정, 충돌 회피, 버킷 궤적 생성, 작업 진도 재계획을 단계별 시험과 통합 실증으로 검증한다.",
    "4": "검증된 자율 토공 기술은 건설 현장 생산성, 작업자 안전, 장비 운영 데이터의 재사용성을 함께 높인다.",
}
_FIGURE_CLAIMS = {
    "0": (
        "현장 지형 관측값과 목표 지형 명세를 안전 정지 조건이 포함된 작업 계약으로 연결한다.",
    ),
    "1": (
        "측량 갱신 결과를 토공 계획의 지형 입력으로 순환시킨다.",
        "토공 계획의 절토량과 성토량을 장비 작업 순서로 변환한다.",
        "버킷 궤적과 장비 자세를 기계 제어 명령으로 동기화한다.",
        "굴착 결과 검증값을 다음 작업 계획에 폐루프로 반영한다.",
    ),
    "2": (
        "지형 오차와 작업 시간을 동일한 반복 시험 절차에서 측정한다.",
        "안전 개입 횟수와 에너지 사용량을 운용 성과와 함께 판정한다.",
    ),
    "3": (
        "다중 센서 관측을 융합해 작업면의 최신 지형 모델을 갱신한다.",
        "버킷 반력을 이용해 토질 상태와 굴착 저항을 추정한다.",
        "장비와 작업자 사이의 충돌 위험을 예측해 안전 궤적을 선택한다.",
        "목표 지형과 장비 제약을 만족하는 버킷 궤적을 생성한다.",
        "실제 작업 진도와 계획 편차를 비교해 남은 작업을 재계획한다.",
        "단계별 시험 결과를 통합 현장 실증의 합격 판정으로 연결한다.",
    ),
    "4": (
        "검증된 자율 토공 기술로 현장 생산성과 작업자 안전을 함께 높인다.",
        "장비 운용 기록을 후속 현장의 계획과 검증에 재사용한다.",
    ),
}
_SECTION_SLOTS = tuple(len(_FIGURE_CLAIMS[str(index)]) for index in range(5))
_PROMPT_RULE = "no text, no labels, no numerals"


def _distribute(blocks: list[str], group_count: int) -> list[list[str]]:
    groups: list[list[str]] = [[] for _ in range(group_count)]
    total = sum(len(block) for block in blocks)
    cumulative = 0
    index = 0
    for position, block in enumerate(blocks):
        if index < group_count - 1 and groups[index]:
            reserved = group_count - index - 1
            filled_share = cumulative >= total * (index + 1) / group_count
            if filled_share or len(blocks) - position <= reserved:
                index += 1
        groups[index].append(block)
        cumulative += len(block)
    return groups


def _fill_body(body: str, section_id: str, figure_ids: list[str], budget: int) -> str:
    del section_id, budget
    blocks = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    if not blocks:
        raise ValueError("section body is empty")
    if [
        match.group(1) for block in blocks for match in FIG_TOKEN_RE.finditer(block)
    ] == figure_ids:
        return "\n\n".join(blocks)

    paragraphs: list[str] = []
    for figure_id, group in zip(figure_ids, _distribute(blocks, len(figure_ids)), strict=True):
        if not group:
            paragraphs.append(f"[[FIG:{figure_id}]]")
            continue
        paragraphs.append(f"[[FIG:{figure_id}]] {group[0]}")
        paragraphs.extend(group[1:])
    return "\n\n".join(paragraphs)



def augment(version_dir: Path) -> None:
    out = version_dir / "out"
    drafts_path = out / "drafts.json"
    document = cast(dict[str, object], json.loads(drafts_path.read_text(encoding="utf-8")))
    sections = cast(list[dict[str, object]], document["sections"])
    profile = PROFILES["30-page"]

    pms = cast(
        dict[str, object],
        json.loads((out / "drafts.json.pms.json").read_text(encoding="utf-8")),
    )
    ledger = cast(dict[str, str], pms.get("ledger", {}))
    public_ids = sorted(
        source_id for source_id, status in ledger.items() if status == "PUBLIC"
    )
    if len(public_ids) < 5:
        raise ValueError("gold replay PMS must provide at least five PUBLIC evidence units")

    figures: list[FigureSpec] = []
    band_index = 0
    ids_by_section: dict[str, list[str]] = {}
    for section_id, slot_count in enumerate(_SECTION_SLOTS):
        section_key = str(section_id)
        ids: list[str] = []
        for local_index, claim in enumerate(_FIGURE_CLAIMS[section_key], start=1):
            figure_id = f"fig-s{section_id}-{local_index:02d}"
            ids.append(figure_id)
            figures.append(
                FigureSpec(
                    figure_id=figure_id,
                    section_id=section_key,
                    source_claim_ids=(public_ids[section_id],),
                    prompt=(
                        "technical editorial diagram on a clean neutral background. "
                        f"Section topic: {_SECTION_THEMES[section_key]} "
                        f"Depicts: {claim}\n{_PROMPT_RULE}"
                    ),
                    caption=claim,
                    png_sha256="",
                    band_index=band_index,
                )
            )
            band_index += 1
        assert len(ids) == slot_count
        ids_by_section[section_key] = ids

    budgets = {str(spec.section_id): spec.prose_char_budget for spec in profile.sections}
    for section in sections:
        section_id = cast(str, section["section_id"])
        body = cast(str, section["body"])
        section["body"] = _fill_body(body, section_id, ids_by_section[section_id], budgets[section_id])
        section["prose_char_budget"] = budgets[section_id]
        section["claims"] = [
            {
                "text": claim,
                "source_ids": [public_ids[int(section_id)]],
            }
            for claim in _FIGURE_CLAIMS[section_id]
        ]
        section["optional_paragraphs"] = [
            {
                "id": f"s{section_id}-extended-validation",
                "text": _SECTION_THEMES[section_id],
                "priority": 100 + int(section_id),
                "included": False,
            }
        ]

    tables = (
        TableSpec(
            table_id="prior-research",
            section_id="1",
            kind="prior-research",
            header=("선행연구·기관·연도", "대상·핵심 방법·정량 성과", "한계 및 본 연구의 차별성"),
            rows=tuple(
                (f"공개 근거 C{index:02d}", "자율 굴착 구성요소 검증", "목표 지형 폐루프 통합 검증")
                for index in range(1, 7)
            ),
            source_ids=tuple(f"C{index:02d}" for index in range(1, 7)),
        ),
    )
    outputs = (
        (drafts_path, json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"),
        (version_dir / "figures.json", figures_to_json(figures) + "\n"),
        (version_dir / "tables.json", tables_to_json(tables) + "\n"),
    )
    for path, content in outputs:
        _ = path.write_text(content, encoding="utf-8")
        path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("version_dir", type=Path)
    args = parser.parse_args()
    version_dir = cast(Path, args.version_dir)
    augment(version_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
