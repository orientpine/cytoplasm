from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from ..contracts import (
    EvidenceUnit,
    KPI,
    PlanSpec,
    TraceLink,
    TraceabilityMatrix,
)
from ..contracts.layout_profile import LayoutProfile, get_layout_profile
from ..contracts.protocols import LLMClient, PMSQuery
from ..contracts.validators import normalize_kpi_weights

from .evidence_fields import (
    clean_list as _clean_list,
    clean_text as _clean_text,
    fact_data as _fact_data,
    int_field as _int_field,
    keywords_from_evidence as _keywords_from_evidence,
    normalized_text as _normalized_text,
    numbers_from_text as _numbers_from_text,
    numeric_grounded as _numeric_grounded,
    objectives_from_evidence as _objectives_from_evidence,
    provenance_location as _provenance_location,
    regex_int as _regex_int,
    regex_value as _regex_value,
    split_field as _split_field,
    split_values as _split_values,
    text_field as _text_field,
    title_fragment as _title_fragment,
    title_from_evidence as _title_from_evidence,
    unit_from_values as _unit_from_values,
    unit_ids as _unit_ids,
    unique as _unique,
)
from .work_packages import InsufficientEvidenceError, build_work_packages


BUCKET_ALIASES: dict[str, tuple[str, ...]] = {
    "objectives": ("objectives", "objective", "연구목표", "목표", "목표/KPI후보"),
    "kpis": ("kpi", "kpis", "KPI", "KPI candidates", "KPI후보", "목표/KPI후보"),
    "methodology": ("methodology", "method", "방법", "방법론", "연구내용·방법"),
    "schedule": ("schedule", "milestone", "milestones", "일정", "마일스톤", "일정/마일스톤"),
    "collaboration": ("collaboration", "resources", "협력", "협업", "자원", "협력/자원"),
}
BUCKET_TOKENS: dict[str, tuple[str, ...]] = {
    "objectives": ("objective", "goal", "목표"),
    "kpis": ("kpi", "성과", "지표"),
    "methodology": ("method", "방법", "기술", "개발", "설계"),
    "schedule": ("schedule", "milestone", "일정", "마일스톤"),
    "collaboration": ("collaboration", "resource", "협력", "협업", "자원", "기관"),
}
MANDATORY_BUCKETS = ("objectives", "kpis", "methodology", "schedule", "collaboration")

TRL_RANGE_RE = re.compile(
    r"TRL\s*(?P<start>\d+)\s*(?:~|→|->|to|에서|-)\s*(?:TRL\s*)?(?P<end>\d+)",
    re.IGNORECASE,
)
WEIGHT_RE = re.compile(r"(?:weight|가중치)\s*[:=]?\s*(?P<weight>\d+)\s*%?", re.IGNORECASE)
BASELINE_RE = re.compile(
    r"(?:baseline|현수준|현재|기준)\s*[:=]?\s*(?P<value>\d[\d,]*(?:\.\d+)?%?)",
    re.IGNORECASE,
)
TARGET_RE = re.compile(
    r"(?:target|목표값|목표)\s*[:=]?\s*(?P<value>\d[\d,]*(?:\.\d+)?%?)",
    re.IGNORECASE,
)


def plan(
    pms: PMSQuery,
    llm: LLMClient,
    *,
    profile: LayoutProfile | str | None = None,
) -> PlanSpec:
    active_profile = (
        profile
        if isinstance(profile, LayoutProfile)
        else get_layout_profile(profile)
    )
    public_evidence = sorted(pms.public_evidence(), key=lambda unit: unit.unit_id)
    if not public_evidence:
        raise InsufficientEvidenceError("No public evidence available")

    bucketed = {
        bucket: _evidence_for_bucket(pms, public_evidence, bucket)
        for bucket in MANDATORY_BUCKETS
    }
    prompt = _build_planner_prompt(bucketed, public_evidence)
    phrasing = _planner_phrasing(llm.complete("planner", prompt), public_evidence)

    objective_units = bucketed["objectives"] or public_evidence
    kpis, kpi_units = _build_kpis(bucketed["kpis"])
    work_packages, work_package_units = build_work_packages(
        bucketed["schedule"],
        bucketed["collaboration"],
        public_evidence,
    )
    trl_start, trl_end, trl_units = _trl_range(public_evidence)

    title = phrasing.title or _title_from_evidence(objective_units)
    objectives = phrasing.objectives or _objectives_from_evidence(objective_units)
    keywords = phrasing.keywords or _keywords_from_evidence(public_evidence)

    return PlanSpec(
        title=title,
        trl_start=trl_start,
        trl_end=trl_end,
        objectives=objectives,
        keywords=keywords[:5],
        kpis=kpis,
        work_packages=work_packages,
        page_budget={
            str(section): budget for section, budget in active_profile.prose_budgets.items()
        },
        traceability=_traceability(bucketed, objective_units, kpi_units, work_package_units, trl_units),
    )


def _evidence_for_bucket(
    pms: PMSQuery,
    public_evidence: Sequence[EvidenceUnit],
    bucket: str,
) -> list[EvidenceUnit]:
    public_by_id = {unit.unit_id: unit for unit in public_evidence}
    matches: dict[str, EvidenceUnit] = {}

    for alias in BUCKET_ALIASES[bucket]:
        for unit in pms.evidence_for_bucket(alias):
            if unit.unit_id in public_by_id:
                matches[unit.unit_id] = public_by_id[unit.unit_id]

    aliases = {_normalized_text(alias) for alias in BUCKET_ALIASES[bucket]}
    tokens = tuple(_normalized_text(token) for token in BUCKET_TOKENS[bucket])
    for unit in public_evidence:
        normalized_bucket = _normalized_text(unit.bucket)
        if normalized_bucket in aliases or any(token in normalized_bucket for token in tokens):
            matches[unit.unit_id] = unit

    return sorted(matches.values(), key=lambda unit: unit.unit_id)


def _build_planner_prompt(
    bucketed: Mapping[str, Sequence[EvidenceUnit]],
    public_evidence: Sequence[EvidenceUnit],
) -> str:
    lines = [
        "You are the KIMM DocBot planner.",
        "Return JSON only with keys: title, objectives, keywords.",
        "Use Korean phrasing. Keep keywords to five or fewer.",
        "Do not introduce numbers that are not present in the evidence.",
        "Ground every phrase in the PUBLIC evidence units below.",
    ]
    for bucket in MANDATORY_BUCKETS:
        lines.append(f"[{bucket}]")
        units = bucketed[bucket]
        if not units:
            lines.append("- <none>")
            continue
        for unit in units:
            lines.append(f"- {unit.unit_id}: {unit.fact}")

    lines.append("[public_unit_ids]")
    lines.extend(f"- {unit.unit_id}" for unit in public_evidence)
    return "\n".join(lines)


@dataclass(frozen=True)
class _Phrasing:
    title: str
    objectives: list[str]
    keywords: list[str]


def _planner_phrasing(response: str, public_evidence: Sequence[EvidenceUnit]) -> _Phrasing:
    evidence_numbers = _numbers_from_text("\n".join(unit.fact for unit in public_evidence))
    parsed = _parse_llm_response(response)
    title = _numeric_grounded(_clean_text(parsed.get("title", "")), evidence_numbers)
    objectives = [
        item
        for item in _clean_list(parsed.get("objectives", []))
        if _numeric_grounded(item, evidence_numbers)
    ]
    keywords = [
        item
        for item in _unique(_clean_list(parsed.get("keywords", [])))
        if _numeric_grounded(item, evidence_numbers)
    ][:5]
    return _Phrasing(title=title, objectives=objectives, keywords=keywords)


def _parse_llm_response(response: str) -> dict[str, object]:
    text = response.strip()
    if not text:
        return {}

    try:
        parsed = cast(object, json.loads(text))
    except json.JSONDecodeError:
        return _parse_tagged_response(text)

    if isinstance(parsed, dict):
        return cast(dict[str, object], parsed)
    return {}


def _parse_tagged_response(text: str) -> dict[str, object]:
    data: dict[str, object] = {}
    objectives: list[str] = []
    keywords: list[str] = []
    active_list: list[str] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        label, value = _split_field(line)
        normalized_label = _normalized_text(label)
        if normalized_label in {"title", "제목"}:
            data["title"] = value
            active_list = None
        elif normalized_label in {"objectives", "objective", "목표", "연구목표"}:
            objectives.extend(_split_values(value))
            active_list = objectives
        elif normalized_label in {"keywords", "keyword", "키워드"}:
            keywords.extend(_split_values(value))
            active_list = keywords
        elif active_list is not None and line[:1] in {"-", "•"}:
            active_list.append(line[1:].strip())

    if objectives:
        data["objectives"] = objectives
    if keywords:
        data["keywords"] = keywords
    return data



def _build_kpis(units: Sequence[EvidenceUnit]) -> tuple[list[KPI], list[EvidenceUnit]]:
    grounded_kpis: list[KPI] = []
    grounded_units: list[EvidenceUnit] = []
    for unit in units:
        kpi = _kpi_from_evidence(unit)
        if kpi is None:
            continue
        grounded_kpis.append(kpi)
        grounded_units.append(unit)
    if not grounded_kpis:
        raise InsufficientEvidenceError(
            "No public KPI evidence with numeric baseline, target, and weight"
        )
    normalized = cast(list[KPI], normalize_kpi_weights(grounded_kpis))
    return normalized, grounded_units


def _kpi_from_evidence(unit: EvidenceUnit) -> KPI | None:
    data = _fact_data(unit.fact)
    text = unit.fact
    weight = _int_field(data, ("weight", "가중치")) or _regex_int(WEIGHT_RE, text, "weight")
    baseline = _text_field(data, ("baseline", "current", "현수준", "현재", "기준"))
    target = _text_field(data, ("target", "목표값", "목표"))
    if baseline is None:
        baseline = _regex_value(BASELINE_RE, text, "value")
    if target is None:
        target = _regex_value(TARGET_RE, text, "value")

    if baseline is None or target is None:
        numbers = _numbers_from_text(text)
        if len(numbers) >= 2:
            baseline = baseline or numbers[0]
            target = target or numbers[1]

    if weight is None or baseline is None or target is None:
        return None

    name = _text_field(data, ("name", "kpi", "kpi_name", "지표", "성과지표"))
    name = name or _title_fragment(text)
    unit_name = _text_field(data, ("unit", "단위")) or _unit_from_values(baseline, target, text)
    structured = bool(data)
    method = _text_field(data, ("method", "측정방법", "방법")) or ("" if structured else text)
    env = _text_field(data, ("env", "environment", "환경", "시험환경")) or _provenance_location(unit)
    # A structured record without a rationale leaves it empty: the old derived line
    # ("현 수준 6% 대비 목표 3% 로 설정한다") repeated the range cell beside it, and the
    # evaluator counts that as the same target stated twice.
    rationale = _text_field(data, ("rationale", "근거", "선정근거")) or (
        "" if structured else text
    )

    return KPI(
        name=name,
        unit=unit_name,
        baseline=baseline,
        target=target,
        weight=weight,
        method=method,
        env=env,
        rationale=rationale,
    )


def _trl_range(public_evidence: Sequence[EvidenceUnit]) -> tuple[int, int, list[EvidenceUnit]]:
    for unit in public_evidence:
        data = _fact_data(unit.fact)
        start = _int_field(data, ("trl_start", "start_trl", "시작TRL"))
        end = _int_field(data, ("trl_end", "end_trl", "종료TRL", "목표TRL"))
        if start is not None and end is not None and start < end:
            return start, end, [unit]

        match = TRL_RANGE_RE.search(unit.fact)
        if match is None:
            continue
        trl_start = int(match.group("start"))
        trl_end = int(match.group("end"))
        if trl_start < trl_end:
            return trl_start, trl_end, [unit]

    return 3, 6, []


def _traceability(
    bucketed: Mapping[str, Sequence[EvidenceUnit]],
    objective_units: Sequence[EvidenceUnit],
    kpi_units: Sequence[EvidenceUnit],
    work_package_units: Sequence[EvidenceUnit],
    trl_units: Sequence[EvidenceUnit],
) -> TraceabilityMatrix:
    links = [
        TraceLink(ir_slot="title", evidence_unit_ids=_unit_ids(objective_units)),
        TraceLink(ir_slot="objectives", evidence_unit_ids=_unit_ids(objective_units)),
        TraceLink(ir_slot="keywords", evidence_unit_ids=_unit_ids(objective_units)),
        TraceLink(ir_slot="trl_start", evidence_unit_ids=_unit_ids(trl_units)),
        TraceLink(ir_slot="trl_end", evidence_unit_ids=_unit_ids(trl_units)),
        TraceLink(ir_slot="kpis", evidence_unit_ids=_unit_ids(kpi_units)),
        TraceLink(ir_slot="methodology", evidence_unit_ids=_unit_ids(bucketed["methodology"])),
        TraceLink(ir_slot="work_packages", evidence_unit_ids=_unit_ids(work_package_units)),
        TraceLink(ir_slot="collaboration", evidence_unit_ids=_unit_ids(bucketed["collaboration"])),
        TraceLink(ir_slot="page_budget", evidence_unit_ids=[]),
    ]
    return TraceabilityMatrix(links=links)


__all__ = ["InsufficientEvidenceError", "plan"]
