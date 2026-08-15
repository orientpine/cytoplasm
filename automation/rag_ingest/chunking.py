"""Markdown-aware chunking + tolerant frontmatter parsing (pure logic).

Wiki notes (W2-2) carry exactly five frontmatter keys
(title/tags/created/updated/links, see skills/wiki/scripts/wiki_store.py).
This parser is deliberately tolerant so plain notes without frontmatter also
ingest cleanly.
"""

from __future__ import annotations

import json

DEFAULT_MAX_CHARS = 1500


def _parse_scalar(raw: str) -> str:
    text = raw.strip()
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        try:
            loaded = json.loads(text)
        except ValueError:
            return text.strip('"')
        return str(loaded)
    return text


def _parse_list(raw: str) -> list[str]:
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    items = [_parse_scalar(part) for part in text.split(",")]
    return [item for item in items if item]


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (flat string metadata, markdown body).

    List values (tags/links) are joined with ``,`` because the MCP payload
    metadata type is ``dict[str, str]``.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, str] = {}
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            body = "\n".join(lines[index + 1 :]).strip("\n")
            return meta, body
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if value.startswith("["):
            meta[key] = ",".join(_parse_list(value))
        else:
            meta[key] = _parse_scalar(value)
    return {}, text


def _split_sections(body: str) -> list[str]:
    """Split on markdown headings; keep each heading with its section text."""
    sections: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if line.startswith("#") and current:
            sections.append("\n".join(current).strip("\n"))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current).strip("\n"))
    return [section for section in sections if section.strip()]


def _hard_split(text: str, max_chars: int) -> list[str]:
    return [text[offset : offset + max_chars] for offset in range(0, len(text), max_chars)]


def chunk_markdown(body: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Deterministic heading-aware chunker.

    Sections are greedily packed up to ``max_chars``; oversized sections are
    hard-split. Identical input always yields identical chunks, which keeps
    the (source, content) -> uuid5 point id stable for idempotent upserts.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    stripped = body.strip()
    if not stripped:
        return []
    chunks: list[str] = []
    buffer = ""
    for section in _split_sections(stripped):
        pieces = _hard_split(section, max_chars) if len(section) > max_chars else [section]
        for piece in pieces:
            candidate = f"{buffer}\n\n{piece}" if buffer else piece
            if len(candidate) <= max_chars:
                buffer = candidate
            else:
                if buffer:
                    chunks.append(buffer)
                buffer = piece
    if buffer:
        chunks.append(buffer)
    return chunks
