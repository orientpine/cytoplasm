"""Deterministic findings for the deductions an AI evaluator takes off a proposal.

The 2026-09-30 owner evaluation named concrete, checkable defects rather than
taste: one KPI carried two baselines (6% in the table, 6.46% in the prose), the same
targets and schedule were restated in the summary, the goals, the methods and the
tables, the measurement protocol gave no trial count or formula, and one term was
spelled two ways (버켓/버킷). A model writer cannot be trusted to avoid them, and a
model judge only reports them after the fact, so these checks read the final drafts
and plan and name each defect with the section that carries it. They never rewrite
text: the render step reports them in ``<out>.quality.json`` and the skill loops the
draft until the list is empty.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from ..contracts import KPI, SectionDraft
from .kimm_domain import STANDARD_SPELLINGS

__all__ = [
    "RUBRIC_CRITERIA",
    "RubricFinding",
    "check_rubric",
    "normalize_spellings",
]

# The owner's evaluator: 6 criteria, 20/20/20/15/15/10.
RUBRIC_CRITERIA: Final[dict[str, str]] = {
    "logic": "논리적 일관성",
    "specificity": "구체성 및 명확성",
    "format": "양식 일관성",
    "concision": "간결성 및 전달력",
    "wording": "단어 적절성 및 문체 안정성",
}

# A value quoted near a KPI that differs from the plan by up to this share is a second
# baseline for the same indicator, not a different quantity.
_DRIFT_TOLERANCE: Final = 0.25
_KEY_WINDOW: Final = 160
_PROTOCOL_WINDOW: Final = 400
_NUMBER_RE: Final = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(%|bar|cm|mm|kW|kWh|Hz|ms|초)")
_REPETITION_RE: Final = re.compile(r"\d+\s*(?:회|번|차례|사이클|세트|시행|개\s*시나리오)")
_FORMULA_RE: Final = re.compile(r"산정식|산출식|계산식|[÷×=]|나눈\s*값|나누어|비율로\s*산정")
_MONTH_RANGE_RE: Final = re.compile(r"(\d+)\s*[~∼-]\s*(\d+)\s*개월")
# The summary states each target once; the goal section defines it (2-1 and 2-3).
_KPI_MENTIONS_ALLOWED: Final[dict[str, int]] = {"0": 1, "2": 2}
_NUMBER_SECTIONS_LIMIT: Final = 2
_SCHEDULE_SECTIONS_LIMIT: Final = 2


@dataclass(frozen=True, slots=True)
class RubricFinding:
    code: str
    criterion: str
    section_id: str
    detail: str


def normalize_spellings(text: str) -> str:
    for wrong, right in STANDARD_SPELLINGS:
        text = text.replace(wrong, right)
    return text


def check_rubric(
    drafts: Sequence[SectionDraft],
    kpis: Sequence[KPI],
    cover: Mapping[str, str] | None = None,
) -> tuple[RubricFinding, ...]:
    bodies = {draft.section_id: draft.body for draft in drafts}
    # The cover table sits on the summary page, so a target written there and again in
    # the summary body is the same repetition the evaluator counts inside section 0.
    cover_text = "\n".join(value for _, value in sorted((cover or {}).items()))
    if cover_text:
        bodies["0"] = f"{cover_text}\n{bodies.get('0', '')}"
    findings: list[RubricFinding] = []
    findings.extend(_kpi_value_drift(bodies, kpis))
    findings.extend(_kpi_protocol(bodies.get("2", ""), kpis))
    findings.extend(_kpi_restated(bodies, kpis))
    findings.extend(_number_restated(bodies, kpis))
    findings.extend(_schedule_restated(bodies))
    findings.extend(_term_variants(bodies, kpis))
    return tuple(findings)


def _value(text: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def _keys(kpi: KPI) -> tuple[str, ...]:
    name = kpi.name.strip()
    bare = re.sub(r"\s*\([^)]*\)\s*", " ", name).strip()
    bare = re.sub(r"\s*목표$", "", bare)
    keys = {name, bare, *re.findall(r"\(([^)]+)\)", name)}
    return tuple(sorted((key for key in keys if len(key) >= 2), key=len, reverse=True))


def _unit(kpi: KPI) -> str:
    return kpi.unit.strip()


def _near_key(text: str, position: int, keys: Iterable[str]) -> bool:
    window = text[max(0, position - _KEY_WINDOW) : position]
    return any(key in window for key in keys)


def _plan_values(kpis: Sequence[KPI]) -> set[float]:
    values: set[float] = {float(sum(kpi.weight for kpi in kpis))}
    for kpi in kpis:
        for text in (kpi.baseline, kpi.target, str(kpi.weight)):
            value = _value(text)
            if value is not None:
                values.add(value)
    return values


def _kpi_value_drift(bodies: dict[str, str], kpis: Sequence[KPI]) -> list[RubricFinding]:
    known = _plan_values(kpis)
    findings: list[RubricFinding] = []
    for section_id, body in sorted(bodies.items()):
        for match in _NUMBER_RE.finditer(body):
            number = float(match.group(1))
            if number in known:
                continue
            kpi = _nearest_kpi(body, match.start(), kpis)
            if kpi is None or match.group(2) != _unit(kpi):
                continue
            references = [v for v in (_value(kpi.baseline), _value(kpi.target)) if v]
            if any(abs(number - ref) / ref <= _DRIFT_TOLERANCE for ref in references):
                findings.append(
                    RubricFinding(
                        "KPI_VALUE_DRIFT",
                        RUBRIC_CRITERIA["logic"],
                        section_id,
                        f"{kpi.name}: 본문 {match.group(0).strip()} 이 계획의 "
                        f"{kpi.baseline} → {kpi.target} 와 다르다 — 한 값만 쓴다",
                    )
                )
    return findings


def _nearest_kpi(text: str, position: int, kpis: Sequence[KPI]) -> KPI | None:
    """The KPI named closest before ``position`` — a list of several KPIs attributes
    each value to the name right in front of it, not to the first name of the list."""
    window_start = max(0, position - _KEY_WINDOW)
    window = text[window_start:position]
    best: tuple[int, KPI] | None = None
    for kpi in kpis:
        found = max((window.rfind(key) for key in _keys(kpi)), default=-1)
        if found >= 0 and (best is None or found > best[0]):
            best = (found, kpi)
    return None if best is None else best[1]


def _kpi_protocol(goal_body: str, kpis: Sequence[KPI]) -> list[RubricFinding]:
    findings: list[RubricFinding] = []
    for kpi in kpis:
        context = f"{kpi.method} {kpi.env}"
        for key in _keys(kpi):
            start = goal_body.find(key)
            if start >= 0:
                context += " " + goal_body[start : start + _PROTOCOL_WINDOW]
                break
        missing = [
            label
            for label, pattern in (("반복 시험 횟수", _REPETITION_RE), ("산정식", _FORMULA_RE))
            if pattern.search(context) is None
        ]
        if missing:
            findings.append(
                RubricFinding(
                    "KPI_PROTOCOL_MISSING",
                    RUBRIC_CRITERIA["specificity"],
                    "2",
                    f"{kpi.name}: {'·'.join(missing)} 이(가) 없다 — 2-3 성과지표에 적는다",
                )
            )
    return findings


def _target_pattern(kpi: KPI) -> re.Pattern[str] | None:
    match = re.search(r"\d+(?:\.\d+)?", kpi.target)
    if match is None:
        return None
    return re.compile(rf"(?<![\d.]){re.escape(match.group())}\s*{re.escape(_unit(kpi))}")


def _kpi_restated(bodies: dict[str, str], kpis: Sequence[KPI]) -> list[RubricFinding]:
    findings: list[RubricFinding] = []
    for kpi in kpis:
        pattern = _target_pattern(kpi)
        if pattern is None:
            continue
        for section_id, body in sorted(bodies.items()):
            mentions = sum(
                1 for match in pattern.finditer(body) if _near_key(body, match.start(), _keys(kpi))
            )
            allowed = _KPI_MENTIONS_ALLOWED.get(section_id, 0)
            if mentions > allowed:
                findings.append(
                    RubricFinding(
                        "KPI_RESTATED",
                        RUBRIC_CRITERIA["concision"],
                        section_id,
                        f"{kpi.name} 목표 {kpi.target} 가 이 절에 {mentions}번 — "
                        f"허용 {allowed}번, 나머지는 '2-3절 성과지표'로 가리킨다",
                    )
                )
    return findings


def _number_restated(bodies: dict[str, str], kpis: Sequence[KPI]) -> list[RubricFinding]:
    weights = {float(kpi.weight) for kpi in kpis}
    targets = {v for kpi in kpis if (v := _value(kpi.target)) is not None}
    sections_by_token: dict[str, set[str]] = {}
    for section_id, body in bodies.items():
        for match in _NUMBER_RE.finditer(body):
            if float(match.group(1)) in weights | targets:
                continue
            token = f"{match.group(1)}{match.group(2)}"
            sections_by_token.setdefault(token, set()).add(section_id)
    return [
        RubricFinding(
            "NUMBER_RESTATED",
            RUBRIC_CRITERIA["concision"],
            ",".join(sorted(sections)),
            f"{token} 이 {len(sections)}개 절에 되풀이된다 — 정의한 절 한 곳과 요약문에만 둔다",
        )
        for token, sections in sorted(sections_by_token.items())
        if len(sections) > _NUMBER_SECTIONS_LIMIT
    ]


def _schedule_restated(bodies: dict[str, str]) -> list[RubricFinding]:
    sections_by_range: dict[str, set[str]] = {}
    findings: list[RubricFinding] = []
    for section_id, body in sorted(bodies.items()):
        ranges = [f"{m.group(1)}~{m.group(2)}개월" for m in _MONTH_RANGE_RE.finditer(body)]
        for span in sorted(set(ranges)):
            sections_by_range.setdefault(span, set()).add(section_id)
            if ranges.count(span) > 1:
                findings.append(
                    RubricFinding(
                        "SCHEDULE_RESTATED",
                        RUBRIC_CRITERIA["concision"],
                        section_id,
                        f"일정 {span} 이 이 절에 {ranges.count(span)}번 나온다 — 한 번만 쓴다",
                    )
                )
    findings.extend(
        RubricFinding(
            "SCHEDULE_RESTATED",
            RUBRIC_CRITERIA["concision"],
            ",".join(sorted(sections)),
            f"일정 {span} 이 {len(sections)}개 절에 되풀이된다 — 요약문과 추진 일정에만 둔다",
        )
        for span, sections in sorted(sections_by_range.items())
        if len(sections) > _SCHEDULE_SECTIONS_LIMIT
    )
    return findings


def _term_variants(bodies: dict[str, str], kpis: Sequence[KPI]) -> list[RubricFinding]:
    corpus = {**bodies, "plan": " ".join(f"{k.name} {k.method} {k.env} {k.rationale}" for k in kpis)}
    findings: list[RubricFinding] = []
    for wrong, right in STANDARD_SPELLINGS:
        where = sorted(section for section, text in corpus.items() if wrong in text)
        if where:
            findings.append(
                RubricFinding(
                    "TERM_VARIANT",
                    RUBRIC_CRITERIA["wording"],
                    ",".join(where),
                    f"'{wrong}' 대신 표준 표기 '{right}' 로 통일한다",
                )
            )
    return findings
