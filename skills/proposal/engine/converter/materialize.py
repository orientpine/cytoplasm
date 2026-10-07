from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping

from ..contracts import DocRecord, EvidenceUnit, Provenance, SensitivityFlag
from ..contracts.ids import stable_id


SEGMENT_RE = re.compile(r"(?:(?<!\d)\.|\.(?!\d)|[。\n])+")
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
PERCENT_WORD_RE = re.compile(r"(\d+(?:\.\d+)?)\s*퍼센트")
MONTH_RE = re.compile(r"(\d+)\s*개월")

BUCKET_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("배경/필요성", ("필요", "현황", "문제점", "배경", "동향")),
    ("목표/KPI후보", ("목표", "KPI", "성능", "정확도", "달성")),
    ("방법론", ("방법", "기술", "알고리즘", "개발", "설계")),
    ("일정/마일스톤", ("일정", "마일스톤", "월", "분기", "완료")),
    ("협력/자원", ("협력", "파트너", "기관", "자원", "인력")),
    ("기대효과", ("기대", "효과", "파급", "활용")),
    ("리스크", ("리스크", "위험", "문제")),
)
SENSITIVITY_ORDER = (
    SensitivityFlag.NONE,
    SensitivityFlag.NDA,
    SensitivityFlag.IP,
    SensitivityFlag.PII,
)


def materialize(docs: list[DocRecord]) -> list[EvidenceUnit]:
    segmented: list[tuple[str, Provenance]] = []
    sensitivity_by_source: dict[str, SensitivityFlag] = {}
    for doc in docs:
        sensitivity_by_source[doc.doc_id] = doc.sensitivity_flag
        segmented.extend(_segment(doc))

    units = _dedup(segmented)
    units = [_with_inherited_sensitivity(unit, sensitivity_by_source) for unit in units]
    return sorted(_detect_conflict(units), key=lambda unit: unit.unit_id)


def _segment(doc: DocRecord) -> list[tuple[str, Provenance]]:
    units: list[tuple[str, Provenance]] = []
    for i, part in enumerate(SEGMENT_RE.split(doc.raw_text)):
        fact_text = part.strip()
        if not fact_text:
            continue

        units.append(
            (
                fact_text,
                Provenance(
                    source_id=doc.doc_id,
                    verbatim=fact_text[:200],
                    location=f"{doc.source_type}:{i}",
                    speaker=doc.participants[0] if doc.participants else None,
                    sender=doc.author,
                    datetime=doc.datetime,
                    source_url=doc.source_url,
                ),
            )
        )
    return units


def _classify_bucket(fact: str) -> str:
    for bucket, keywords in BUCKET_KEYWORDS:
        if any(keyword in fact for keyword in keywords):
            return bucket
    return "배경/필요성"


def _dedup(units: list[tuple[str, Provenance]]) -> list[EvidenceUnit]:
    grouped: dict[str, list[tuple[str, Provenance]]] = defaultdict(list)
    for fact, provenance in units:
        grouped[_unit_id(fact)].append((fact, provenance))

    evidence_units: list[EvidenceUnit] = []
    for unit_id, grouped_units in grouped.items():
        fact = grouped_units[0][0]
        provenances = _unique_provenances([provenance for _, provenance in grouped_units])
        evidence_units.append(
            EvidenceUnit(
                unit_id=unit_id,
                fact=fact,
                provenances=provenances,
                bucket=_classify_bucket(fact),
                sensitivity_flag=SensitivityFlag.NONE,
            )
        )

    return sorted(evidence_units, key=lambda unit: unit.unit_id)


def _detect_conflict(units: list[EvidenceUnit]) -> list[EvidenceUnit]:
    grouped: dict[str, list[EvidenceUnit]] = defaultdict(list)
    for unit in units:
        if unit.sensitivity_flag != SensitivityFlag.NONE:
            continue
        numbers = _numeric_values(unit.fact)
        if numbers:
            grouped[_entity_key(unit.fact)].append(unit)

    conflict_ids: set[str] = set()
    for grouped_units in grouped.values():
        source_values: dict[str, set[str]] = defaultdict(set)
        for unit in grouped_units:
            for provenance in unit.provenances:
                source_values[provenance.source_id].update(_numeric_values(provenance.verbatim))

        if len(source_values) < 2:
            continue
        value_sets = {tuple(sorted(values)) for values in source_values.values()}
        if len(value_sets) > 1:
            conflict_ids.update(unit.unit_id for unit in grouped_units)

    return [unit.model_copy(update={"conflict": unit.unit_id in conflict_ids}) for unit in units]


def _with_inherited_sensitivity(
    unit: EvidenceUnit, sensitivity_by_source: Mapping[str, SensitivityFlag]
) -> EvidenceUnit:
    flags = [sensitivity_by_source.get(provenance.source_id, SensitivityFlag.NONE) for provenance in unit.provenances]
    return unit.model_copy(update={"sensitivity_flag": _max_sensitivity(flags)})


def _max_sensitivity(flags: list[SensitivityFlag]) -> SensitivityFlag:
    if not flags:
        return SensitivityFlag.NONE
    return max(flags, key=lambda flag: SENSITIVITY_ORDER.index(flag))


def _unit_id(fact: str) -> str:
    return stable_id(_fact_normalized(fact))


def _fact_normalized(fact: str) -> str:
    normalized = _normalize_text(fact)
    return _canonical_fact(normalized)[:100]


def _canonical_fact(fact: str) -> str:
    if _is_accuracy_kpi(fact):
        month = _first_match(MONTH_RE, fact)
        percent = _first_percent(fact)
        parts = ["문서봇", "근거 추출", "정확도"]
        if percent:
            parts.append(f"{percent}%")
        if month:
            parts.append(f"{month}개월")
        parts.append("목표")
        return " ".join(parts)

    if "협력기관" in fact and "분담금" in fact:
        amount = _first_number(fact)
        return f"협력기관 분담금 {amount}원" if amount else "협력기관 분담금"

    return fact


def _normalize_text(text: str) -> str:
    normalized = PERCENT_WORD_RE.sub(r"\1%", text.lower().strip())
    return " ".join(normalized.split())


def _is_accuracy_kpi(fact: str) -> bool:
    evidence_terms = ("근거" in fact or "증거" in fact) and ("추출" in fact or "식별" in fact)
    return evidence_terms and "정확도" in fact


def _first_percent(fact: str) -> str | None:
    for number in _numeric_values(fact):
        if number.endswith("%"):
            return number[:-1]
    return _first_number(fact)


def _first_number(fact: str) -> str | None:
    numbers = _numeric_values(fact)
    return numbers[0] if numbers else None


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


def _numeric_values(fact: str) -> tuple[str, ...]:
    normalized = _normalize_text(fact)
    return tuple(match.group(0).replace(",", "") for match in NUMBER_RE.finditer(normalized))


def numeric_values(fact: str) -> tuple[str, ...]:
    return _numeric_values(fact)


def _entity_key(fact: str) -> str:
    normalized = _normalize_text(fact)
    if _is_accuracy_kpi(normalized):
        return "문서봇 근거 추출 정확도"
    if "협력기관" in normalized and "분담금" in normalized:
        return "협력기관 분담금"
    if "달성률" in normalized:
        return "달성률"

    prefix = NUMBER_RE.split(normalized, maxsplit=1)[0]
    return prefix.strip() or normalized


def entity_key(fact: str) -> str:
    return _entity_key(fact)


def _unique_provenances(provenances: list[Provenance]) -> list[Provenance]:
    unique: dict[tuple[str, str, str], Provenance] = {}
    for provenance in provenances:
        key = (provenance.source_id, provenance.location, provenance.verbatim)
        if key not in unique:
            unique[key] = provenance
    return sorted(unique.values(), key=lambda item: (item.source_id, item.location, item.verbatim))


__all__ = [
    "_classify_bucket",
    "_dedup",
    "_detect_conflict",
    "_segment",
    "entity_key",
    "materialize",
    "numeric_values",
]
