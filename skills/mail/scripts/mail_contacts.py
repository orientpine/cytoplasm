"""Display names for mail header address lists (digest cards).

The mailon runtime stores headers twice-wrapped: the webmail already returns
``"\\"Name\\"" <addr>``-style display names, and the markdown frontmatter
YAML-escapes that again. Showing the raw value made the owner digest print
``\\"Name\\"`` with stacked backslashes (2026-10-07/08). This module peels every
layer exactly once and keeps only what a reader needs: the name, or the address
when no name exists.

stdlib only and pure — no I/O, no exceptions for malformed input.
"""

from __future__ import annotations

import re
from email.header import decode_header, make_header

_ANGLE = re.compile(r"<\s*([^<>\s]+@[^<>\s]+)\s*>")
_BARE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_NAME_EDGE = "\"'\\ \t,;"
_NAME_LIMIT = 40


def yaml_scalar(value: str) -> str:
    """Undo one YAML double-quoted scalar layer (``"…"`` with ``\\\\``/``\\"``)."""
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        inner = text[1:-1]
        if text[0] == '"':
            return re.sub(r'\\(["\\])', r"\1", inner)
        return inner.replace("''", "'")
    return text


def _decode_words(text: str) -> str:
    if "=?" not in text:
        return text
    try:
        return str(make_header(decode_header(text)))
    except (ValueError, LookupError, UnicodeDecodeError):
        return text


def clean_name(raw: str) -> str:
    """Strip every quote/backslash wrapping layer from a display name."""
    name = _decode_words(raw)
    previous = None
    while previous != name:
        previous = name
        name = re.sub(r'\\+(["\\])', r"\1", name).strip(_NAME_EDGE)
    return " ".join(name.split())


def _split_last_unquoted_comma(segment: str) -> tuple[str, str]:
    """``(before, after)`` around the last comma outside double quotes."""
    in_quote = False
    cut = -1
    escaped = False
    for index, char in enumerate(segment):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            in_quote = not in_quote
        elif char == "," and not in_quote:
            cut = index
    if cut < 0:
        return "", segment
    return segment[:cut], segment[cut + 1:]


def parse_contacts(raw: str) -> list[tuple[str, str]]:
    """``[(name, address)]`` in header order; ``name`` may be empty."""
    text = _decode_words(str(raw or ""))
    contacts: list[tuple[str, str]] = []
    position = 0
    for matched in _ANGLE.finditer(text):
        before, name_part = _split_last_unquoted_comma(text[position:matched.start()])
        contacts.extend(("", address) for address in _BARE.findall(before))
        name = clean_name(name_part)
        address = matched.group(1).strip().lower()
        if name.lower() == address or _BARE.fullmatch(name):
            name = ""
        contacts.append((name, address))
        position = matched.end()
    contacts.extend(("", address.lower()) for address in _BARE.findall(text[position:]))
    return contacts


def display(name: str, address: str) -> str:
    shown = name or address
    return shown if len(shown) <= _NAME_LIMIT else shown[: _NAME_LIMIT - 1] + "…"


def sender_label(raw: str) -> str:
    """Sender as a reader sees it: the name, else the address, else ''."""
    contacts = parse_contacts(raw)
    if not contacts:
        return clean_name(str(raw or ""))[:_NAME_LIMIT]
    return display(*contacts[0])


def cc_label(raw: str | tuple[str, ...], *, owner: str = "", shown: int = 3) -> str:
    """``"A, B, C 외 N명"`` — owner removed, duplicates collapsed."""
    joined = ", ".join(raw) if isinstance(raw, tuple) else str(raw or "")
    owner_address = owner.strip().lower()
    seen: set[str] = set()
    labels: list[str] = []
    for name, address in parse_contacts(joined):
        if address == owner_address or address in seen:
            continue
        seen.add(address)
        labels.append(display(name, address))
    if len(labels) <= shown:
        return ", ".join(labels)
    return f"{', '.join(labels[:shown])} 외 {len(labels) - shown}명"
