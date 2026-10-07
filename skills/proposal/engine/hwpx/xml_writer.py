from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from html import unescape
from xml.etree import ElementTree as ET

from ..contracts import AnchorMap, NodeRef
from .anchor_map import NAMESPACES, load_anchor_map

PREVIEW_ENTRY = "Preview/PrvText.txt"
SECTION_ENTRY = "Contents/section0.xml"
HP_T_TAG = "{http://www.hancom.co.kr/hwpml/2011/paragraph}t"
HP_P_TAG = "{http://www.hancom.co.kr/hwpml/2011/paragraph}p"
HP_P_START = b"<hp:p"
HP_P_END = b"</hp:p>"
HP_T_START = b"<hp:t"
HP_T_END = b"</hp:t>"

__all__ = [
    "NestedParagraphError",
    "blank_direct_paragraphs",
    "clone_paragraph",
    "load_anchor_map",
    "insert_paragraph_fragment",
    "plain_direct_ordinals",
    "remove_direct_paragraphs",
    "section_property_direct_ordinals",
    "set_direct_paragraph_char_properties",
    "set_text",
    "sync_prv_text",
]

_TEXT_ELEMENT_RE = re.compile(rb"<hp:t(?:\s[^>]*)?>.*?</hp:t>", re.DOTALL)
_RUN_CHAR_REF_RE = re.compile(rb'(<hp:run charPrIDRef=")\d+(")')


class NestedParagraphError(ValueError):
    """A byte-splice target nests another ``hp:p`` and cannot be reached."""


def set_text(xml_bytes: bytes, slot: str, text: str, anchor_map: AnchorMap) -> bytes:
    node_ref = _node_ref_for(slot, anchor_map)
    root = ET.fromstring(xml_bytes)
    target = _target_text_element(root, node_ref)
    text_ordinal = _ordinal_among(root.iter(HP_T_TAG), target)
    start, end = _text_payload_offsets(xml_bytes, text_ordinal)
    payload = _escape_text(text).encode("utf-8")
    changed = xml_bytes[:start] + payload + xml_bytes[end:]
    return _drop_enclosing_line_layout(changed, start, start + len(payload))


def _rfind_start_tag(xml_bytes: bytes, start_token: bytes, before: int) -> int:
    candidate = before
    while True:
        candidate = xml_bytes.rfind(start_token, 0, candidate)
        if candidate == -1:
            return -1
        next_byte_index = candidate + len(start_token)
        if next_byte_index < len(xml_bytes) and xml_bytes[next_byte_index] in (
            ord(" "),
            ord(">"),
            ord("/"),
        ):
            return candidate


def _drop_enclosing_line_layout(xml_bytes: bytes, text_start: int, text_end: int) -> bytes:
    """Discard the line-layout cache of the paragraph whose text just changed.

    hp:linesegarray records where each line of the OLD text sat. Hangul trusts it,
    so replaced text longer than the cached layout is drawn on the cached lines and
    overlaps. The cache cannot be recomputed here without Hangul's glyph metrics,
    and an absent cache is recomputed on open, so it is removed rather than rebuilt.
    """
    paragraph_start = _rfind_start_tag(xml_bytes, HP_P_START, text_start)
    paragraph_end = xml_bytes.find(HP_P_END, text_end)
    if paragraph_start == -1 or paragraph_end == -1:
        return xml_bytes
    paragraph_end += len(HP_P_END)
    paragraph = xml_bytes[paragraph_start:paragraph_end]
    return xml_bytes[:paragraph_start] + _strip_linesegarray(paragraph) + xml_bytes[paragraph_end:]


def clone_paragraph(xml_bytes: bytes, slot: str, n: int, anchor_map: AnchorMap) -> bytes:
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return xml_bytes

    node_ref = _node_ref_for(slot, anchor_map)
    paragraph_ordinal = _global_paragraph_ordinal(ET.fromstring(xml_bytes), node_ref)
    start, end = _element_offsets(xml_bytes, HP_P_START, HP_P_END, paragraph_ordinal)
    paragraph = xml_bytes[start:end]
    return xml_bytes[:end] + paragraph * n + xml_bytes[end:]


def insert_paragraph_fragment(
    xml_bytes: bytes,
    slot: str,
    fragment: bytes,
    anchor_map: AnchorMap,
) -> bytes:
    """Insert trusted, caller-built paragraph XML immediately after an anchor paragraph."""
    node_ref = _node_ref_for(slot, anchor_map)
    heading_ordinal = _global_paragraph_ordinal(ET.fromstring(xml_bytes), node_ref)
    _, heading_end = _element_offsets(xml_bytes, HP_P_START, HP_P_END, heading_ordinal)
    return xml_bytes[:heading_end] + fragment + xml_bytes[heading_end:]


def remove_direct_paragraphs(xml_bytes: bytes, ordinals: Iterable[int]) -> bytes:
    """Splice direct ``hs:sec`` paragraph children by their sibling ordinal."""
    requested = tuple(sorted(set(ordinals), reverse=True))
    root = ET.fromstring(xml_bytes)
    global_ordinals = _direct_global_ordinals(root, requested)
    _reject_nested_paragraphs(root, requested)
    changed = xml_bytes
    for global_ordinal in sorted(global_ordinals, reverse=True):
        start, end = _element_offsets(changed, HP_P_START, HP_P_END, global_ordinal)
        changed = changed[:start] + changed[end:]
    return changed


def plain_direct_ordinals(xml_bytes: bytes) -> frozenset[int]:
    """Direct paragraphs that nest no other paragraph, so a byte splice can reach them."""
    root = ET.fromstring(xml_bytes)
    return frozenset(
        ordinal
        for ordinal, paragraph in enumerate(_direct_paragraphs(root))
        if all(node is paragraph for node in paragraph.iter(HP_P_TAG))
    )


def section_property_direct_ordinals(xml_bytes: bytes) -> frozenset[int]:
    """Direct paragraphs carrying ``hp:secPr`` — the form's page setup lives there."""
    root = ET.fromstring(xml_bytes)
    return frozenset(
        ordinal
        for ordinal, paragraph in enumerate(
            child for child in list(root) if child.tag == HP_P_TAG
        )
        if paragraph.find(".//hp:secPr", NAMESPACES) is not None
    )


def blank_direct_paragraphs(xml_bytes: bytes, ordinals: Iterable[int]) -> bytes:
    """Empty a direct paragraph's text but keep the paragraph and all it carries."""
    ordered = tuple(sorted(set(ordinals)))
    if not ordered:
        return xml_bytes

    root = ET.fromstring(xml_bytes)
    _reject_nested_paragraphs(root, ordered)
    changed = xml_bytes
    for global_ordinal in sorted(_direct_global_ordinals(root, ordered), reverse=True):
        start, end = _element_offsets(changed, HP_P_START, HP_P_END, global_ordinal)
        emptied = _TEXT_ELEMENT_RE.sub(b"<hp:t></hp:t>", changed[start:end])
        changed = changed[:start] + _strip_linesegarray(emptied) + changed[end:]
    return changed


def set_direct_paragraph_char_properties(
    xml_bytes: bytes,
    assignments: Mapping[int, str],
) -> bytes:
    """Repoint every run of the named direct paragraphs at a character property."""
    if not assignments:
        return xml_bytes

    root = ET.fromstring(xml_bytes)
    ordered = tuple(sorted(assignments))
    _reject_nested_paragraphs(root, ordered)
    pairs = sorted(
        zip(ordered, _direct_global_ordinals(root, ordered), strict=True),
        key=lambda pair: pair[1],
        reverse=True,
    )
    changed = xml_bytes
    for direct_ordinal, global_ordinal in pairs:
        start, end = _element_offsets(changed, HP_P_START, HP_P_END, global_ordinal)
        char_id = assignments[direct_ordinal].encode("ascii")
        repointed = _RUN_CHAR_REF_RE.sub(rb"\g<1>" + char_id + rb"\g<2>", changed[start:end])
        changed = changed[:start] + repointed + changed[end:]
    return changed


def _direct_paragraphs(root: ET.Element) -> list[ET.Element]:
    return [child for child in list(root) if child.tag == HP_P_TAG]


def _direct_global_ordinals(root: ET.Element, ordinals: Sequence[int]) -> list[int]:
    direct = _direct_paragraphs(root)
    if any(ordinal < 0 for ordinal in ordinals):
        raise ValueError("paragraph ordinals must be non-negative")
    out_of_range = [ordinal for ordinal in ordinals if ordinal >= len(direct)]
    if out_of_range:
        raise ValueError(f"direct paragraph ordinal out of range: {max(out_of_range)}")
    every = list(root.iter(HP_P_TAG))
    return [every.index(direct[ordinal]) for ordinal in ordinals]


def _reject_nested_paragraphs(root: ET.Element, ordinals: Sequence[int]) -> None:
    # _element_offsets ends a paragraph at the first </hp:p> after its start tag, so a
    # paragraph that nests one (a table cell, a caption) would be spliced mid-element.
    direct = _direct_paragraphs(root)
    nested = [
        ordinal
        for ordinal in ordinals
        if any(node is not direct[ordinal] for node in direct[ordinal].iter(HP_P_TAG))
    ]
    if nested:
        raise NestedParagraphError(
            f"direct paragraph nests another paragraph: {nested}"
        )


def sync_prv_text(section0_bytes: bytes, old_entries: dict[str, bytes], new_section0: bytes) -> dict[str, bytes]:
    _ = section0_bytes
    updated = old_entries.__class__(old_entries)
    if hasattr(old_entries, "compress_types"):
        updated.compress_types = old_entries.compress_types.copy()
        updated.external_attrs = old_entries.external_attrs.copy()
        updated.create_systems = old_entries.create_systems.copy()
    updated[PREVIEW_ENTRY] = _extract_preview_text(new_section0).encode("utf-8")
    return updated


def _node_ref_for(slot: str, anchor_map: AnchorMap) -> NodeRef:
    try:
        return anchor_map.slots[slot]
    except KeyError as exc:
        raise KeyError(f"unknown anchor slot: {slot}") from exc


def _target_text_element(root: ET.Element, node_ref: NodeRef) -> ET.Element:
    resolved = _resolve(root, node_ref.element_path)
    if resolved is None:
        raise ValueError(f"anchor path does not resolve: {node_ref.element_path}")
    if resolved.tag == HP_T_TAG:
        return resolved

    target = resolved.find(".//hp:t", NAMESPACES)
    if target is None:
        raise ValueError(f"anchor path has no hp:t descendant: {node_ref.element_path}")
    return target


def _resolve(root: ET.Element, element_path: str) -> ET.Element | None:
    current = root
    parts = element_path.split("/")
    if not parts or _expanded_name(_strip_ordinal(parts[0])) != root.tag:
        return None

    for part in parts[1:]:
        name, ordinal = _path_part(part)
        expanded_name = _expanded_name(name)
        candidates = [child for child in list(current) if child.tag == expanded_name]
        if ordinal >= len(candidates):
            return None
        current = candidates[ordinal]
    return current


def _path_part(part: str) -> tuple[str, int]:
    if "[" not in part:
        return part, 0
    name, ordinal_text = part[:-1].split("[", 1)
    return name, int(ordinal_text)


def _expanded_name(prefixed_name: str) -> str:
    if ":" not in prefixed_name:
        return prefixed_name
    prefix, local_name = prefixed_name.split(":", 1)
    return f"{{{NAMESPACES[prefix]}}}{local_name}"


def _strip_ordinal(part: str) -> str:
    return part.split("[", 1)[0]


def _ordinal_among(elements: Iterable[ET.Element], target: ET.Element) -> int:
    for ordinal, element in enumerate(elements):
        if element is target:
            return ordinal
    raise ValueError("target element is not in document order")


def _text_payload_offsets(xml_bytes: bytes, text_ordinal: int) -> tuple[int, int]:
    start, end = _element_offsets(xml_bytes, HP_T_START, HP_T_END, text_ordinal)
    start_close = xml_bytes.find(b">", start, end)
    if start_close == -1:
        raise ValueError("malformed hp:t start tag")
    return start_close + 1, end - len(HP_T_END)


def _element_offsets(xml_bytes: bytes, start_token: bytes, end_token: bytes, ordinal: int) -> tuple[int, int]:
    if ordinal < 0:
        raise ValueError("ordinal must be non-negative")

    start = -1
    search_from = 0
    for _ in range(ordinal + 1):
        start = _find_start_tag(xml_bytes, start_token, search_from)
        if start == -1:
            raise ValueError(f"could not find occurrence {ordinal} of {start_token!r}")
        search_from = start + len(start_token)

    end = xml_bytes.find(end_token, search_from)
    if end == -1:
        raise ValueError(f"could not find end token {end_token!r}")
    return start, end + len(end_token)


def _find_start_tag(xml_bytes: bytes, start_token: bytes, search_from: int) -> int:
    candidate = search_from
    while True:
        candidate = xml_bytes.find(start_token, candidate)
        if candidate == -1:
            return -1
        next_byte_index = candidate + len(start_token)
        if next_byte_index < len(xml_bytes) and xml_bytes[next_byte_index] in (ord(" "), ord(">"), ord("/")):
            return candidate
        candidate = next_byte_index


def _global_paragraph_ordinal(root: ET.Element, node_ref: NodeRef) -> int:
    target = _target_text_element(root, node_ref)
    for ordinal, paragraph in enumerate(root.iter(HP_P_TAG)):
        if any(element is target for element in paragraph.iter()):
            return ordinal
    raise ValueError(f"anchor path is not inside an hp:p: {node_ref.element_path}")


def _strip_linesegarray(paragraph: bytes) -> bytes:
    return re.sub(
        rb"<hp:linesegarray(?:\s[^>]*)?>.*?</hp:linesegarray>",
        b"",
        paragraph,
        flags=re.DOTALL,
    )


def _escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _extract_preview_text(section0_bytes: bytes) -> str:
    root = ET.fromstring(section0_bytes)
    lines: list[str] = []
    for paragraph in root.findall(".//hp:p", NAMESPACES):
        text = "".join(unescape(text_node.text or "") for text_node in paragraph.findall(".//hp:t", NAMESPACES)).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)
