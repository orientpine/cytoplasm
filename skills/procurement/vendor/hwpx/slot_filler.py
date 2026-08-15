"""Adapted honeypot deterministic paragraph-ID HWPX slot filler."""
from __future__ import annotations

from pathlib import Path

from skills.procurement.vendor.hwpx.zip_surgery import HwpxSurgeon, xml_escape

OPEN_P = "<hp:p"
CLOSE_P = "</hp:p>"


def _find_next_paragraph_open(section_text: str, start: int) -> int:
    position = section_text.find(OPEN_P, start)
    while position != -1:
        marker_end = position + len(OPEN_P)
        if marker_end < len(section_text) and section_text[marker_end] in (" ", ">", "/"):
            return position
        position = section_text.find(OPEN_P, marker_end)
    return -1


def _find_paragraph_span(section_text: str, start: int) -> tuple[int, int, int] | None:
    opening_end = section_text.find(">", start)
    if opening_end == -1:
        return None
    opening_end += 1
    depth, cursor = 0, opening_end
    while True:
        next_open = _find_next_paragraph_open(section_text, cursor)
        next_close = section_text.find(CLOSE_P, cursor)
        if next_close == -1:
            return None
        if next_open != -1 and next_open < next_close:
            nested_end = section_text.find(">", next_open)
            if nested_end == -1:
                return None
            depth += section_text[nested_end - 1] != "/"
            cursor = nested_end + 1
            continue
        if depth == 0:
            return opening_end, next_close, next_close + len(CLOSE_P)
        depth -= 1
        cursor = next_close + len(CLOSE_P)


def _escape_text_content(text: str) -> str:
    return xml_escape(text).replace("\r\n", "&#10;").replace("\r", "&#13;").replace("\n", "&#10;")


def _normalized(section_text: str) -> str:
    if not section_text.startswith("<?xml"):
        return section_text.replace("\r\n", "").replace("\r", "").replace("\n", "")
    declaration_end = section_text.find("?>")
    if declaration_end == -1:
        return section_text.replace("\r\n", "").replace("\r", "").replace("\n", "")
    declaration = section_text[:declaration_end]
    if "standalone=" not in declaration:
        declaration = f"{declaration} standalone='no'"
    body = section_text[declaration_end + 2:].lstrip("\r\n").replace("\r\n", "").replace("\r", "").replace("\n", "")
    return f"{declaration}?>\n{body}"


def _runs(runs: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    for style, text in runs:
        escaped = _escape_text_content(text)
        text_node = f"<hp:t>{escaped}</hp:t>" if escaped else "<hp:t/>"
        parts.append(f'<hp:run charPrIDRef="{style}">{text_node}</hp:run>')
    return "".join(parts)


def fill_slots_by_paragraph_id(section_bytes: bytes, fills: dict[str, list[tuple[str, str]]]) -> tuple[bytes, list[str]]:
    """Replace uniquely addressed paragraphs and report IDs that cannot be resolved."""
    section = section_bytes.decode("utf-8")
    unresolved: list[str] = []
    for paragraph_id, runs in fills.items():
        needle = f'<hp:p id="{paragraph_id}"'
        start = section.find(needle)
        if start == -1 or section.find(needle, start + len(needle)) != -1:
            unresolved.append(paragraph_id)
            continue
        span = _find_paragraph_span(section, start)
        if span is None:
            unresolved.append(paragraph_id)
            continue
        opening_end, _closing_start, paragraph_end = span
        replacement = f"{section[start:opening_end]}{_runs(runs)}{CLOSE_P}"
        section = f"{section[:start]}{replacement}{section[paragraph_end:]}"
    return _normalized(section).encode("utf-8"), unresolved


def fill_hwpx(input_path: Path, fills: dict[str, list[tuple[str, str]]], output_path: Path) -> list[str]:
    """Persist deterministic paragraph fills through the vendored archive surgeon."""
    surgeon = HwpxSurgeon(input_path)
    modified, unresolved = fill_slots_by_paragraph_id(surgeon.section_bytes, fills)
    surgeon.replace_text({surgeon.section_bytes.decode("utf-8"): modified.decode("utf-8")})
    _ = surgeon.save(output_path)
    return unresolved
