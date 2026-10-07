from __future__ import annotations

import re
import zipfile

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Final
from xml.etree import ElementTree as ET

from ..contracts import AnchorMap, PlanSpec
from .anchor_map import (
    DEFAULT_SEED_PATH,
    remap_after_direct_paragraph_deletion,
)
from .typography import BODY, MAJOR_HEADING, SUB_HEADING
from .xml_writer import (
    blank_direct_paragraphs,
    plain_direct_ordinals,
    remove_direct_paragraphs,
    section_property_direct_ordinals,
    set_direct_paragraph_char_properties,
    set_text,
)

META_DIRECT_PARAGRAPH_ORDINALS: Final = (0, 1, 2, 3)
BULLET_DIRECT_PARAGRAPH_ORDINALS: Final = (
    19,
    20,
    21,
    23,
    24,
    26,
    27,
    28,
    33,
    34,
    38,
    39,
    40,
    44,
    45,
    46,
    47,
    50,
    51,
    52,
    54,
    55,
    63,
    64,
    65,
    67,
    68,
    69,
    72,
    73,
    74,
)
INSTRUCTION_DIRECT_PARAGRAPH_ORDINALS: Final = (48,)
SECTION_HEADING_DIRECT_ORDINALS: Final = (5, 16, 30, 42, 60)
GUIDANCE_DIRECT_PARAGRAPH_ORDINALS: Final = frozenset(
    (
        *META_DIRECT_PARAGRAPH_ORDINALS,
        *BULLET_DIRECT_PARAGRAPH_ORDINALS,
        *INSTRUCTION_DIRECT_PARAGRAPH_ORDINALS,
    )
)

_SECTION0_ENTRY: Final = "Contents/section0.xml"
_SUMMARY_SECTION_ID: Final = "0"
_LAST_COVER_SLOT: Final = "cover.keywords"

_COVER_FIELDS: Final = (
    "title",
    "classification",
    "period",
    "trl",
    "final_goal",
    "contents",
    "annual_goal",
    "expected_effect",
    "keywords",
)
_COVER_LABELS: Final = {
    "title": "과제명 (국문/영문): ",
    "classification": "연구 분야·기술분류: ",
    "period": "전체 연구기간: ",
    "trl": "기술성숙도 (TRL): ",
    "final_goal": "최종 목표: ",
    "contents": "전체 연구내용: ",
    "annual_goal": "연차별 목표: ",
    "expected_effect": "성과 활용계획 및 기대효과: ",
    "keywords": "핵심어 (국문/영문): ",
}

__all__ = [
    "BULLET_DIRECT_PARAGRAPH_ORDINALS",
    "GUIDANCE_DIRECT_PARAGRAPH_ORDINALS",
    "INSTRUCTION_DIRECT_PARAGRAPH_ORDINALS",
    "META_DIRECT_PARAGRAPH_ORDINALS",
    "SECTION_HEADING_DIRECT_ORDINALS",
    "anchored_direct_ordinals",
    "body_anchor_slot",
    "cover_values_from_planspec",
    "FORM_SUBHEADINGS",
    "deletable_guidance_ordinals",
    "duplicated_subheading_ordinals",
    "fill_seed_cover",
    "form_section_headings",
    "remove_seed_guidance",
    "restyle_seed_form_runs",
    "trim_trailing_blank_paragraphs",
]


_DIRECT_PARAGRAPH_RE: Final = re.compile(r"^hs:sec/hp:p\[(\d+)\]")


def anchored_direct_ordinals(anchor_map: AnchorMap) -> frozenset[int]:
    ordinals: set[int] = set()
    for node_ref in anchor_map.slots.values():
        match = _DIRECT_PARAGRAPH_RE.match(node_ref.element_path)
        if match is not None:
            ordinals.add(int(match.group(1)))
    return frozenset(ordinals)


def deletable_guidance_ordinals(anchor_map: AnchorMap) -> frozenset[int]:
    """Guidance minus paragraphs an anchor writes into.

    A slot whose target paragraph disappears is dropped by the remapper, and
    write_traceability_table catches KeyError, so the appendix would vanish with
    no error at all. Deleting text is never worth losing a write target.
    """
    return GUIDANCE_DIRECT_PARAGRAPH_ORDINALS - anchored_direct_ordinals(anchor_map)


def body_anchor_slot(section_id: str, anchor_map: AnchorMap) -> str:
    """Where a section's prose is inserted.

    The summary section heading is followed by the nine cover fields, so
    anchoring its body to the heading pushes 과제명 and 연구기간 below the whole
    summary. Anchor it under the last cover field instead.
    """
    if section_id == _SUMMARY_SECTION_ID and _LAST_COVER_SLOT in anchor_map.slots:
        return _LAST_COVER_SLOT
    return f"section.{section_id}.heading"


# Derived from the seed once and pinned by a test: the form outline it requires,
# as (direct-child ordinal, section id, exact title).
FORM_SUBHEADINGS: Final = (
    (18, "1", "1-1. 기술적 배경 및 국내외 동향"),
    (22, "1", "1-2. 시장·정책적 필요성"),
    (25, "1", "1-3. 기존 기술(선행 연구) 대비 한계 및 차별성"),
    (32, "2", "2-1. 최종 목표"),
    (35, "2", "2-2. 단계별·연차별 목표"),
    (37, "2", "2-3. 성과지표 (KPI) 및 평가주안점"),
    (43, "3", "3-1. 세부 연구 내용 (연차별 / Work Package별)"),
    (49, "3", "3-2. 추진 전략 및 방법론"),
    (53, "3", "3-3. 추진 일정"),
    (62, "4", "4-1. 활용 방안"),
    (66, "4", "4-2. 기대효과"),
    (71, "4", "4-3. 후속 연구 및 사업화 계획"),
)


@lru_cache(maxsize=4)
def form_section_headings(seed_path: str | Path | None = None) -> tuple[str, ...]:
    """The form's top-level headings, read from the seed in section order.

    FORM_SUBHEADINGS pins the sub-heading outline; the five 대제목 are read live
    instead, so a form that rewords one is still recognised as carrying that
    section rather than silently reading as "섹션 누락".
    """
    path = DEFAULT_SEED_PATH if seed_path is None else Path(seed_path)
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read(_SECTION0_ENTRY))
    paragraphs = [child for child in root if child.tag == f"{_HP_NS}p"]
    headings: list[str] = []
    for ordinal in SECTION_HEADING_DIRECT_ORDINALS:
        if ordinal >= len(paragraphs):
            raise ValueError(f"seed has no direct paragraph {ordinal}: {path}")
        text = "".join(node.text or "" for node in paragraphs[ordinal].iter(f"{_HP_NS}t"))
        if not text.strip():
            raise ValueError(f"seed heading paragraph {ordinal} is empty: {path}")
        headings.append(" ".join(text.split()))
    return tuple(headings)


def duplicated_subheading_ordinals(section_bodies: Mapping[str, str]) -> frozenset[int]:
    """Seed sub-headings the body already carries, so keeping both would duplicate.

    A body that omits one keeps the seed copy: the form requires that outline, and
    losing it without a word is worse than a section the body left thin.
    """
    return frozenset(
        ordinal
        for ordinal, section_id, title in FORM_SUBHEADINGS
        if title in section_bodies.get(section_id, "")
    )

def restyle_seed_form_runs(xml_bytes: bytes) -> bytes:
    """Move the seed's own headings and cover fields onto the form's typography.

    Run this before anything is spliced out: the ordinals are the pristine seed's,
    and one deletion renumbers every sibling after it.
    """
    assignments = {
        **{ordinal: BODY.char_id for ordinal in plain_direct_ordinals(xml_bytes)},
        **{ordinal: SUB_HEADING.char_id for ordinal, _, _ in FORM_SUBHEADINGS},
        **{ordinal: MAJOR_HEADING.char_id for ordinal in SECTION_HEADING_DIRECT_ORDINALS},
    }
    return set_direct_paragraph_char_properties(xml_bytes, assignments)


def remove_seed_guidance(
    xml_bytes: bytes,
    anchor_map: AnchorMap,
    section_bodies: Mapping[str, str] | None = None,
) -> tuple[bytes, AnchorMap]:
    """Delete frozen seed guidance paragraphs and return anchors remapped to the result."""
    removable = deletable_guidance_ordinals(anchor_map)
    if section_bodies is not None:
        removable |= duplicated_subheading_ordinals(section_bodies) - anchored_direct_ordinals(
            anchor_map
        )
    # hp:secPr rides inside the opening guidance paragraph and holds the section's
    # page size, margins and outline shape for the whole document. Splicing that
    # paragraph out hands Hangul back its own defaults, so the artifact stops being
    # the form even though every word survives. Empty it instead.
    protected = section_property_direct_ordinals(xml_bytes)
    deletable = tuple(sorted(removable - protected))
    changed = blank_direct_paragraphs(xml_bytes, sorted(removable & protected))
    changed = remove_direct_paragraphs(changed, deletable)
    remapped = remap_after_direct_paragraph_deletion(changed, anchor_map, deletable)
    return changed, remapped


_HP_NS: Final = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


def _is_blank_paragraph(paragraph: ET.Element) -> bool:
    if any((node.text or "").strip() for node in paragraph.iter(f"{_HP_NS}t")):
        return False
    return all(
        paragraph.find(f".//{_HP_NS}{name}") is None for name in ("tbl", "pic", "secPr")
    )


def trim_trailing_blank_paragraphs(xml_bytes: bytes, anchor_map: AnchorMap) -> bytes:
    """Delete the blank paragraph run at the section tail.

    The seed ends in empty spacer paragraphs that carry nothing once guidance is
    emptied, and enough of them spill over into an entirely blank final page.
    Only the maximal blank suffix goes, and the scan stops at the first anchor
    write target or hp:secPr carrier — every deletion then sits after every
    survivor, so no surviving ordinal shifts and the anchor map needs no remap.
    """
    paragraphs = [
        child for child in ET.fromstring(xml_bytes) if child.tag == f"{_HP_NS}p"
    ]
    protected = anchored_direct_ordinals(anchor_map) | section_property_direct_ordinals(
        xml_bytes
    )
    removable: list[int] = []
    for ordinal in range(len(paragraphs) - 1, -1, -1):
        if ordinal in protected or not _is_blank_paragraph(paragraphs[ordinal]):
            break
        removable.append(ordinal)
    if not removable:
        return xml_bytes
    return remove_direct_paragraphs(xml_bytes, sorted(removable))

def cover_values_from_planspec(
    planspec: PlanSpec,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Derive factual cover values, leaving facts absent from PlanSpec blank."""
    packages = sorted(planspec.work_packages, key=lambda item: item.wp_id)
    package_summary = "; ".join(item.title for item in packages)
    total_months = planspec.total_months
    kpi_effects = "; ".join(
        f"{kpi.name} {_target_with_unit(kpi.target, kpi.unit)} 달성" for kpi in planspec.kpis
    )
    values = {
        "title": planspec.title,
        "classification": "",
        "period": f"총 {total_months}개월" if packages and total_months > 0 else "",
        "trl": f"{planspec.trl_start}단계 → {planspec.trl_end}단계",
        "final_goal": "; ".join(planspec.objectives),
        "contents": package_summary,
        "annual_goal": package_summary,
        "expected_effect": kpi_effects,
        "keywords": ", ".join(planspec.keywords),
    }
    if overrides is None:
        return values

    unknown = sorted(set(overrides) - set(_COVER_FIELDS))
    if unknown:
        raise ValueError(f"unknown cover fields: {', '.join(unknown)}")
    for field in _COVER_FIELDS:
        if field in overrides:
            values[field] = overrides[field]
    return values


def fill_seed_cover(
    xml_bytes: bytes,
    anchor_map: AnchorMap,
    planspec: PlanSpec,
    overrides: Mapping[str, str] | None = None,
) -> bytes:
    """Replace all nine seed cover prompts through slot-based text writes."""
    values = cover_values_from_planspec(planspec, overrides)
    changed = xml_bytes
    for field in _COVER_FIELDS:
        changed = set_text(
            changed,
            f"cover.{field}",
            f"{_COVER_LABELS[field]}{values[field]}",
            anchor_map,
        )
    return changed


def _target_with_unit(target: str, unit: str) -> str:
    if not unit or target.endswith(unit):
        return target
    return f"{target}{unit}"
