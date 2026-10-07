from __future__ import annotations

import json
import re
import zipfile
from bisect import bisect_left
from collections.abc import Iterable
from pathlib import Path
from typing import Final, cast
from xml.etree import ElementTree as ET

from ..contracts import AnchorMap, NodeRef

HP_NS: Final = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HS_NS: Final = "http://www.hancom.co.kr/hwpml/2011/section"

NAMESPACES: Final = {
    "hp": HP_NS,
    "hs": HS_NS,
}
URI_TO_PREFIX: Final = {uri: prefix for prefix, uri in NAMESPACES.items()}

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_ANCHOR_MAP_PATH: Final = PROJECT_ROOT / "resource" / "anchor_map.json"
DEFAULT_SEED_PATH: Final = PROJECT_ROOT / "resource" / "R&D 연구계획서 양식.hwpx"

SECTION_HEADING_TEXT: Final = (
    "0. 연구 요약문",
    "1. 연구 배경 및 필요성",
    "2. 연구 목표",
    "3. 연구 내용 및 수행 방법",
    "4. 기대효과 및 활용 방안",
)
SUMMARY_SLOTS: Final = {
    "summary.project_title": "과제명",
    "summary.classification": "연구 분야·기술분류",
    "summary.period": "전체 연구기간",
    "summary.trl": "기술성숙도",
    "summary.final_goal": "최종 목표",
    "summary.contents": "전체 연구내용",
    "summary.annual_goal": "연차별 목표",
    "summary.expected_effect": "성과 활용계획 및 기대효과",
    "summary.keywords": "핵심어",
}
COVER_SLOTS: Final = {
    "cover.title": "과제명",
    "cover.classification": "연구 분야·기술분류",
    "cover.period": "전체 연구기간",
    "cover.trl": "기술성숙도",
    "cover.final_goal": "최종 목표",
    "cover.contents": "전체 연구내용",
    "cover.annual_goal": "연차별 목표",
    "cover.expected_effect": "성과 활용계획 및 기대효과",
    "cover.keywords": "핵심어",
}


def build_anchor_map(section0_xml: bytes) -> AnchorMap:
    root = ET.fromstring(section0_xml)
    context = _XmlContext(root)
    slots: dict[str, NodeRef] = {}

    paragraphs = root.findall(".//hp:p", NAMESPACES)
    heading_by_text = _first_text_node_by_paragraph_text(paragraphs)
    for section_index, heading_text in enumerate(SECTION_HEADING_TEXT):
        slots[f"section.{section_index}.heading"] = context.node_ref(heading_by_text[heading_text])

    summary_by_prefix = _first_text_node_by_prefix(paragraphs)
    for slot, prefix in {**SUMMARY_SLOTS, **COVER_SLOTS}.items():
        slots[slot] = context.node_ref(summary_by_prefix[prefix])

    tables = root.findall(".//hp:tbl", NAMESPACES)
    if len(tables) < 2:
        raise ValueError(f"expected at least 2 hp:tbl elements, found {len(tables)}")

    _add_kpi_slots(slots, context, tables[0])
    _add_gantt_slots(slots, context, tables[1])

    return AnchorMap(slots=dict(sorted(slots.items())))


def load_anchor_map(path: str | None = None) -> AnchorMap:
    anchor_path = Path(path) if path is not None else DEFAULT_ANCHOR_MAP_PATH
    data = cast(dict[str, object], json.loads(anchor_path.read_text(encoding="utf-8")))
    return AnchorMap.model_validate(data)


def remap_after_direct_paragraph_deletion(
    section0_xml: bytes,
    anchor_map: AnchorMap,
    deleted_ordinals: Iterable[int],
) -> AnchorMap:
    """Remap paths and global indexes after direct ``hs:sec`` paragraphs are removed."""
    deleted = tuple(sorted(set(deleted_ordinals)))
    context = _XmlContext.from_xml(section0_xml)
    slots: dict[str, NodeRef] = {}
    pattern = re.compile(r"^hs:sec/hp:p\[(\d+)\](?P<tail>/.*)$")

    for slot, node_ref in sorted(anchor_map.slots.items()):
        match = pattern.fullmatch(node_ref.element_path)
        if match is None:
            raise ValueError(f"anchor is not under a direct hs:sec paragraph: {slot}")
        old_ordinal = int(match.group(1))
        if old_ordinal in deleted:
            continue
        new_ordinal = old_ordinal - bisect_left(deleted, old_ordinal)
        new_path = f"hs:sec/hp:p[{new_ordinal}]{match.group('tail')}"
        element = context.resolve(new_path)
        if element is None:
            raise ValueError(f"remapped anchor path does not resolve: {slot}: {new_path}")
        slots[slot] = context.node_ref(element)

    return AnchorMap(slots=slots)


def save_anchor_map(anchor_map: AnchorMap, path: str | None = None) -> None:
    anchor_path = Path(path) if path is not None else DEFAULT_ANCHOR_MAP_PATH
    anchor_path.parent.mkdir(parents=True, exist_ok=True)
    _ = anchor_path.write_text(
        json.dumps(
            anchor_map.model_dump(exclude_none=True), ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n",
        encoding="utf-8",
    )


def verify_against_section(anchor_map: AnchorMap, section0_xml: bytes) -> bool:
    try:
        context = _XmlContext.from_xml(section0_xml)
        for node_ref in anchor_map.slots.values():
            element = context.resolve(node_ref.element_path)
            if element is None or context.index(element) != node_ref.index:
                return False
    except ET.ParseError:
        return False
    return True


def verify_against_seed(anchor_map: AnchorMap, seed_path: str | None = None) -> bool:
    hwpx_path = Path(seed_path) if seed_path is not None else DEFAULT_SEED_PATH
    try:
        with zipfile.ZipFile(hwpx_path) as seed:
            section_xml = seed.read("Contents/section0.xml")
        return verify_against_section(anchor_map, section_xml)
    except (KeyError, OSError, zipfile.BadZipFile):
        return False


class _XmlContext:
    def __init__(self, root: ET.Element) -> None:
        self.root: ET.Element = root
        self._parents: dict[ET.Element, ET.Element] = {
            child: parent for parent in root.iter() for child in list(parent)
        }
        self._elements: list[ET.Element] = list(root.iter())
        self._indexes: dict[ET.Element, int] = {
            element: index for index, element in enumerate(self._elements)
        }

    @classmethod
    def from_xml(cls, section0_xml: bytes) -> _XmlContext:
        return cls(ET.fromstring(section0_xml))

    def node_ref(self, element: ET.Element) -> NodeRef:
        current: ET.Element | None = element
        ancestor_table_id: str | None = None
        while current is not None:
            if current.tag == f"{{{HP_NS}}}tbl":
                ancestor_table_id = current.attrib.get("id")
                break
            current = self._parents.get(current)
        return NodeRef(
            element_path=self.path(element),
            index=self.index(element),
            ancestor_table_id=ancestor_table_id,
        )

    def index(self, element: ET.Element) -> int:
        return self._indexes[element]

    def path(self, element: ET.Element) -> str:
        parts: list[str] = []
        current: ET.Element | None = element
        while current is not None:
            name = _prefixed_name(current.tag)
            parent = self._parents.get(current)
            if parent is not None:
                same_tag_siblings = [child for child in list(parent) if child.tag == current.tag]
                name = f"{name}[{same_tag_siblings.index(current)}]"
            parts.append(name)
            current = parent
        return "/".join(reversed(parts))

    def resolve(self, element_path: str) -> ET.Element | None:
        parts = element_path.split("/")
        if not parts or _strip_ordinal(parts[0]) != _prefixed_name(self.root.tag):
            return None

        current = self.root
        for part in parts[1:]:
            match = re.fullmatch(r"([^\[]+)(?:\[(\d+)\])?", part)
            if match is None:
                return None
            prefixed_tag, ordinal_text = match.groups()
            ordinal = int(ordinal_text) if ordinal_text is not None else 0
            expanded_tag = _expanded_name(prefixed_tag)
            candidates = [child for child in list(current) if child.tag == expanded_tag]
            if ordinal >= len(candidates):
                return None
            current = candidates[ordinal]
        return current


def _add_kpi_slots(slots: dict[str, NodeRef], context: _XmlContext, table: ET.Element) -> None:
    rows = table.findall("./hp:tr", NAMESPACES)
    column_names = ("stage", "name", "target")
    header_cells = rows[0].findall("./hp:tc", NAMESPACES)
    for column_index, cell in enumerate(header_cells):
        slots[f"kpi.header.{column_names[column_index]}"] = context.node_ref(cell)

    for row_index, row in enumerate(rows[1:]):
        cells = row.findall("./hp:tc", NAMESPACES)
        for column_index, cell in enumerate(cells):
            slots[f"kpi.row.{row_index}.{column_names[column_index]}"] = context.node_ref(cell)


def _add_gantt_slots(slots: dict[str, NodeRef], context: _XmlContext, table: ET.Element) -> None:
    rows = table.findall("./hp:tr", NAMESPACES)
    for header_index, cell in enumerate(rows[0].findall("./hp:tc", NAMESPACES), start=1):
        slots[f"gantt.header.month.{header_index}"] = context.node_ref(cell)

    for row_index, row in enumerate(rows[1:]):
        cells = row.findall("./hp:tc", NAMESPACES)
        slots[f"gantt.row.{row_index}.activity"] = context.node_ref(cells[0])
        for month_index, cell in enumerate(cells, start=1):
            slots[f"gantt.row.{row_index}.month.{month_index}"] = context.node_ref(cell)


def _first_text_node_by_paragraph_text(paragraphs: list[ET.Element]) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}
    for paragraph in paragraphs:
        text_nodes = paragraph.findall(".//hp:t", NAMESPACES)
        paragraph_text = "".join(text_node.text or "" for text_node in text_nodes).strip()
        if paragraph_text and paragraph_text not in result and text_nodes:
            result[paragraph_text] = text_nodes[0]
    return result


def _first_text_node_by_prefix(paragraphs: list[ET.Element]) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}
    for paragraph in paragraphs:
        text_nodes = paragraph.findall(".//hp:t", NAMESPACES)
        paragraph_text = "".join(text_node.text or "" for text_node in text_nodes).strip()
        for prefix in SUMMARY_SLOTS.values():
            if prefix not in result and paragraph_text.startswith(prefix) and text_nodes:
                result[prefix] = text_nodes[0]
    return result


def _prefixed_name(tag: str) -> str:
    if not tag.startswith("{"):
        return tag
    uri, local_name = tag[1:].split("}", 1)
    prefix = URI_TO_PREFIX.get(uri)
    if prefix is None:
        raise ValueError(f"unsupported XML namespace URI: {uri}")
    return f"{prefix}:{local_name}"


def _expanded_name(prefixed_name: str) -> str:
    if ":" not in prefixed_name:
        return prefixed_name
    prefix, local_name = prefixed_name.split(":", 1)
    uri = NAMESPACES[prefix]
    return f"{{{uri}}}{local_name}"


def _strip_ordinal(part: str) -> str:
    return part.split("[", 1)[0]
