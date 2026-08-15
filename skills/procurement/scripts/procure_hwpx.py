"""HWPX registry analysis and deterministic fill adapter."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Final
import xml.etree.ElementTree as ET
import zipfile

from skills.procurement.scripts.procure_core import PLACEHOLDER
from skills.procurement.vendor.hwpx import form_mapper, slot_filler
from skills.procurement.vendor.hwpx.fix_namespaces import fix_hwpx_namespaces
from skills.procurement.vendor.hwpx.zip_surgery import validate_surgery

SECTION_XML: Final = "Contents/section0.xml"
NS: Final = {"hp": "http://www.hancom.co.kr/hwpml/2011/paragraph"}


class HwpxFillError(RuntimeError):
    """A mapped HWPX slot cannot be filled safely and deterministically."""


def _field_name(label: str | None, number: int, seen: set[str]) -> str:
    candidate = (label or "").strip().rstrip(":：") or f"slot_{number:02d}"
    field = candidate
    suffix = 2
    while field in seen:
        field = f"{candidate}_{suffix}"
        suffix += 1
    seen.add(field)
    return field


def analyze(template: Path) -> dict[str, object]:
    """Create a semantic, fully offline form map from vendored addressing data."""
    form_map, warnings = form_mapper.build_form_map(template)
    slots = form_map["slots"]
    if not isinstance(slots, list):
        raise HwpxFillError("HWPX form map slots are invalid")
    seen: set[str] = set()
    for number, slot in enumerate(slots, start=1):
        if not isinstance(slot, dict):
            raise HwpxFillError("HWPX form map slot is invalid")
        label = slot.get("label_association")
        label_text = label if isinstance(label, str) else None
        slot["slot_id"] = _field_name(label_text, number, seen)
        slot["slot_type"] = "empty_input"
        slot["zone"] = "detail"
        slot["confidence"] = "high" if label_text else "medium"
    form_map["confidence"] = "high" if not warnings and all(slot.get("label_association") for slot in slots if isinstance(slot, dict)) else "medium"
    form_map["warnings"] = warnings
    return form_map


def field_names(form_map: dict[str, object]) -> tuple[str, ...]:
    """Return the stable human-facing keys represented by semantic HWPX slots."""
    slots = form_map.get("slots")
    if not isinstance(slots, list):
        raise HwpxFillError("HWPX form map slots are invalid")
    return tuple(str(slot["slot_id"]) for slot in slots if isinstance(slot, dict) and isinstance(slot.get("slot_id"), str))


def write_map(form_map: dict[str, object], output: Path) -> None:
    """Persist a canonical map that is reusable without future model calls."""
    output.write_text(json.dumps(form_map, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output.chmod(0o600)


def _section(template: Path) -> ET.Element:
    with zipfile.ZipFile(template) as archive:
        return ET.fromstring(archive.read(SECTION_XML))


def _legacy_fills(template: Path, fields: dict[str, str]) -> dict[str, list[tuple[str, str]]]:
    fills: dict[str, list[tuple[str, str]]] = {}
    for paragraph in _section(template).iterfind(".//hp:p", NS):
        text = "".join(paragraph.itertext())
        if "{{" not in text:
            continue
        paragraph_id = paragraph.get("id")
        if not paragraph_id:
            raise HwpxFillError("legacy HWPX placeholder paragraph has no deterministic id")
        rendered = PLACEHOLDER.sub(lambda match: fields.get(match[1].strip(), match[0]), text)
        fills[paragraph_id] = [("0", rendered)]
    return fills


def _semantic_fills(form_map: dict[str, object], fields: dict[str, str]) -> dict[str, list[tuple[str, str]]]:
    fills: dict[str, list[tuple[str, str]]] = {}
    slots = form_map.get("slots")
    if not isinstance(slots, list):
        raise HwpxFillError("HWPX form map slots are invalid")
    for slot in slots:
        if not isinstance(slot, dict) or not isinstance(slot.get("slot_id"), str):
            raise HwpxFillError("HWPX form map slot is invalid")
        field = slot["slot_id"]
        if field not in fields:
            continue
        addressing = slot.get("addressing")
        if not isinstance(addressing, dict) or addressing.get("method") != "paragraph_id":
            raise HwpxFillError(f"HWPX slot '{field}' has no unique paragraph address")
        paragraph_id = addressing.get("paragraph_id")
        if not isinstance(paragraph_id, str):
            raise HwpxFillError(f"HWPX slot '{field}' has no paragraph id")
        fills[paragraph_id] = [("0", fields[field])]
    return fills


def fill(template: Path, fields: dict[str, str], output: Path, form_map_path: Path | None = None) -> None:
    """Fill stored semantic maps or legacy placeholders through slot_filler only."""
    if form_map_path is None:
        form_map = analyze(template)
        fills = _semantic_fills(form_map, fields) if field_names(form_map) else _legacy_fills(template, fields)
    else:
        raw = json.loads(form_map_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise HwpxFillError("stored HWPX form map is invalid")
        fills = _semantic_fills(raw, fields)
    unresolved = slot_filler.fill_hwpx(template, fills, output)
    if unresolved:
        output.unlink(missing_ok=True)
        raise HwpxFillError(f"HWPX slot unresolved: {', '.join(unresolved)}")
    fix_hwpx_namespaces(output)
    errors = validate_surgery(template, output)
    if errors:
        output.unlink(missing_ok=True)
        raise HwpxFillError(f"HWPX surgery validation failed: {'; '.join(errors)}")
