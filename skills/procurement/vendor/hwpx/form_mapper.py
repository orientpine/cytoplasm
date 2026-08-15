"""Adapted honeypot form mapper: deterministic HWPX empty-slot addressing."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

from skills.procurement.vendor.hwpx import analyze_template as at

SCHEMA_VERSION = "1.0.0"
SECTION_XML = "Contents/section0.xml"
HEADER_XML = "Contents/header.xml"
PLACEHOLDER_SYMBOLS = frozenset({"◦", "○", "•", "-", "※", "·", "□", "■"})


@dataclass(frozen=True, slots=True)
class LocatedSlot:
    """A deterministic address plus its nearest static field label."""

    paragraph_id: str | None
    table_index: int | None
    row: int | None
    col: int | None
    label: str | None


def is_empty_input(text: str) -> bool:
    """Return whether an HWPX paragraph is an intentionally empty input slot."""
    stripped = text.strip()
    return not stripped or stripped in PLACEHOLDER_SYMBOLS


def _number(value: str | None, fallback: int) -> int:
    try:
        return int(value) if value is not None else fallback
    except ValueError:
        return fallback


def _address(cell: ET.Element, row: int, col: int) -> tuple[int, int]:
    address = cell.find("hp:cellAddr", at.NS)
    if address is None:
        return row, col
    return _number(address.get("rowAddr"), row), _number(address.get("colAddr"), col)


def _cell_text(cell: ET.Element) -> str:
    return at.get_text(cell.find("hp:subList", at.NS)).strip()


def _nearest_label(cells: list[tuple[int, int, ET.Element]], row: int, col: int) -> str | None:
    left = [(cell_col, _cell_text(cell)) for cell_row, cell_col, cell in cells if cell_row == row and cell_col < col]
    labels = [(cell_col, text) for cell_col, text in left if text and not is_empty_input(text) and len(text) <= 30]
    if labels:
        return max(labels)[1]
    above = [(cell_row, _cell_text(cell)) for cell_row, cell_col, cell in cells if cell_col == col and cell_row < row]
    headers = [(cell_row, text) for cell_row, text in above if text and not is_empty_input(text) and len(text) <= 30]
    return max(headers)[1] if headers else None


def _table_slots(section: ET.Element) -> list[LocatedSlot]:
    slots: list[LocatedSlot] = []
    for table_index, table in enumerate(section.findall(".//hp:tbl", at.NS)):
        cells: list[tuple[int, int, ET.Element]] = []
        for fallback_row, row in enumerate(table.findall("hp:tr", at.NS)):
            for fallback_col, cell in enumerate(row.findall("hp:tc", at.NS)):
                actual_row, actual_col = _address(cell, fallback_row, fallback_col)
                cells.append((actual_row, actual_col, cell))
        for row, col, cell in cells:
            sublist = cell.find("hp:subList", at.NS)
            if sublist is None:
                continue
            label = _nearest_label(cells, row, col)
            prior = [at.get_text(paragraph).strip() for paragraph in sublist.findall("hp:p", at.NS)]
            for index, paragraph in enumerate(sublist.findall("hp:p", at.NS)):
                text = at.get_text(paragraph)
                local_label = next((value for value in reversed(prior[:index]) if value and not is_empty_input(value)), label)
                if is_empty_input(text):
                    slots.append(LocatedSlot(paragraph.get("id"), table_index, row, col, local_label))
    return slots


def _body_slots(section: ET.Element) -> list[LocatedSlot]:
    slots: list[LocatedSlot] = []
    label: str | None = None
    for paragraph in section.findall("hp:p", at.NS):
        if paragraph.find(".//hp:tbl", at.NS) is not None:
            continue
        text = at.get_text(paragraph).strip()
        if is_empty_input(text):
            slots.append(LocatedSlot(paragraph.get("id"), None, None, None, label))
        elif len(text) <= 30:
            label = text
    return slots


def _section(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as archive:
        members = set(archive.namelist())
        missing = {HEADER_XML, SECTION_XML} - members
        if missing:
            raise ValueError(f"Missing required HWPX XML: {', '.join(sorted(missing))}")
        return ET.fromstring(archive.read(SECTION_XML))


def build_form_map(hwpx_path: str | Path) -> tuple[dict[str, object], list[str]]:
    """Extract repeatable paragraph addresses without semantic reasoning."""
    path = Path(hwpx_path)
    root = _section(path)
    section = root.find(".//hs:sec", at.NS) or root
    slots = [*_table_slots(section), *_body_slots(section)]
    duplicate_ids = {identifier for identifier, count in Counter(slot.paragraph_id for slot in slots if slot.paragraph_id).items() if count > 1}
    mapped: list[dict[str, object]] = []
    for number, slot in enumerate(slots, start=1):
        method = "paragraph_id" if slot.paragraph_id and slot.paragraph_id not in duplicate_ids else "sentinel"
        mapped.append({
            "slot_id": f"slot_{number:02d}", "slot_type": None,
            "addressing": {"method": method, "paragraph_id": slot.paragraph_id if method == "paragraph_id" else None,
                           "cell": None if slot.table_index is None else {"table_index": slot.table_index, "row": slot.row, "col": slot.col}},
            "label_association": slot.label, "zone": None, "confidence": None, "expected_content_hint": None,
        })
    warnings = [f"Duplicate paragraph_id {identifier}; using sentinel addressing" for identifier in sorted(duplicate_ids)]
    return {"schema_version": SCHEMA_VERSION, "source_template": path.name, "slots": mapped, "unresolved": [], "confidence": None}, warnings
