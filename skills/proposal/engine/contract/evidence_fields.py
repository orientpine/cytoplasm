"""Shared evidence field parsing, extracted unchanged from the planner."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import cast

from ..contracts import EvidenceUnit

NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+|[가-힣]{2,}")


def fact_data(text: str) -> dict[str, object]:
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            parsed = cast(object, json.loads(stripped))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            items = cast(Mapping[object, object], parsed).items()
            return {normalized_text(str(key)): value for key, value in items}

    data: dict[str, object] = {}
    for part in re.split(r"[;\n]+", text):
        key, value = split_field(part.strip())
        if key and value:
            data[normalized_text(key)] = value
    return data


def split_field(text: str) -> tuple[str, str]:
    for separator in ("=", ":", "："):
        if separator in text:
            key, value = text.split(separator, 1)
            return key.strip().strip("-• "), value.strip()
    return "", text.strip()


def text_field(data: Mapping[str, object], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = data.get(normalized_text(key))
        if value is None:
            continue
        text = clean_text(value)
        if text:
            return text
    return None


def int_field(data: Mapping[str, object], keys: Sequence[str]) -> int | None:
    text = text_field(data, keys)
    if text is None:
        return None
    match = NUMBER_RE.search(text)
    return int(float(match.group(0).rstrip("%").replace(",", ""))) if match else None


def list_field(data: Mapping[str, object], keys: Sequence[str]) -> list[str]:
    for key in keys:
        value = data.get(normalized_text(key))
        items = clean_list(value) if value is not None else []
        if items:
            return items
    return []


def regex_int(pattern: re.Pattern[str], text: str, group: str) -> int | None:
    value = regex_value(pattern, text, group)
    if value is None:
        return None
    return int(float(value.rstrip("%").replace(",", "")))


def regex_value(pattern: re.Pattern[str], text: str, group: str) -> str | None:
    match = pattern.search(text)
    return match.group(group) if match is not None else None


def regex_text(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(0).replace(" ", "-").upper() if match is not None else None


def clean_text(value: object) -> str:
    return " ".join(str(value).strip().split())


def clean_list(value: object) -> list[str]:
    if isinstance(value, list):
        result: list[str] = []
        for item in cast(list[object], value):
            text = clean_text(item)
            if text:
                result.append(text)
        return result
    return split_values(clean_text(value))


def split_values(text: str) -> list[str]:
    if not text:
        return []
    return [item.strip() for item in re.split(r"[,|/、]\s*", text) if item.strip()]


def numbers_from_text(text: str) -> list[str]:
    return [match.group(0).rstrip("%").replace(",", "") for match in NUMBER_RE.finditer(text)]


def numeric_grounded(text: str, evidence_numbers: Sequence[str]) -> str:
    if not text:
        return ""
    phrase_numbers = numbers_from_text(text)
    if all(number in evidence_numbers for number in phrase_numbers):
        return text
    return ""


def unit_from_values(baseline: str, target: str, text: str) -> str:
    if "%" in baseline or "%" in target or "%" in text:
        return "%"
    for unit in ("건", "개월", "회", "명", "점"):
        if unit in text:
            return unit
    return "evidence"


def provenance_location(unit: EvidenceUnit) -> str:
    if unit.provenances:
        return unit.provenances[0].location
    return unit.unit_id


def title_from_evidence(units: Sequence[EvidenceUnit]) -> str:
    return title_fragment(units[0].fact)


def objectives_from_evidence(units: Sequence[EvidenceUnit]) -> list[str]:
    return [clean_text(unit.fact) for unit in units[:3] if clean_text(unit.fact)]


def keywords_from_evidence(units: Sequence[EvidenceUnit]) -> list[str]:
    keywords: list[str] = []
    for unit in units:
        for match in WORD_RE.finditer(unit.fact):
            word = match.group(0)
            if word.lower() in {"baseline", "target", "weight", "method", "lead"}:
                continue
            keywords.append(word)
            if len(unique(keywords)) >= 5:
                return unique(keywords)[:5]
    return unique(keywords)[:5] or [units[0].unit_id]


def title_fragment(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return "public evidence"
    fragment = re.split(r"[.;。]", cleaned, maxsplit=1)[0]
    return fragment[:80]


def unit_ids(units: Sequence[EvidenceUnit]) -> list[str]:
    return unique([unit.unit_id for unit in units])


def unique(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def normalized_text(text: str) -> str:
    return "".join(str(text).lower().split())

