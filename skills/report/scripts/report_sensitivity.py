"""Deterministic W2-3-compatible sensitivity gate for W5-3."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in (None, ""):
    from report_core import Note
else:
    from .report_core import Note

GLM_PROVIDER: Final = "custom:litellm"
GLM_MODEL: Final = "glm-main"
CODEX_PROVIDER: Final = "openai-codex"
CODEX_MODEL: Final = "gpt-5.4"


@dataclass(frozen=True, slots=True)
class TagRule:
    tag: str
    keywords: tuple[str, ...]
    patterns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Route:
    provider: str
    model: str
    sensitive: bool
    tags: tuple[str, ...]


def _fallback_rules(raw: str) -> tuple[TagRule, ...]:
    tags: dict[str, dict[str, list[str]]] = {}
    current_tag = ""
    current_list: list[str] | None = None
    for line in raw.splitlines():
        content = line.split("#", 1)[0].rstrip()
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip())
        value = content.strip()
        if indent == 2 and value.endswith(":"):
            current_tag = value[:-1]
            tags[current_tag] = {"keywords": [], "patterns": []}
            current_list = None
        elif indent == 4 and value.endswith(":") and current_tag:
            current_list = tags[current_tag].setdefault(value[:-1], [])
        elif value.startswith("- ") and current_list is not None:
            scalar = value[2:].strip().strip('"').replace("\\\\", "\\")
            current_list.append(scalar)
    return tuple(
        TagRule(tag, tuple(spec["keywords"]), tuple(spec["patterns"]))
        for tag, spec in tags.items()
    )


def load_rules(path: Path) -> tuple[TagRule, ...]:
    rules = _fallback_rules(path.read_text(encoding="utf-8"))
    for rule in rules:
        for pattern in rule.patterns:
            re.compile(pattern)
    return rules


def route_notes(notes: tuple[Note, ...], rules: tuple[TagRule, ...]) -> Route:
    """Route all selected notes before an LLM prompt is constructed."""
    tags: list[str] = []
    for note in notes:
        lowered = note.text.lower()
        for rule in rules:
            matched = any(keyword.lower() in lowered for keyword in rule.keywords) or any(
                re.search(pattern, note.text) is not None for pattern in rule.patterns
            )
            if matched and rule.tag not in tags:
                tags.append(rule.tag)
    sensitive = "patent-sensitive" in tags
    return Route(
        CODEX_PROVIDER if sensitive else GLM_PROVIDER,
        CODEX_MODEL if sensitive else GLM_MODEL,
        sensitive,
        tuple(tags),
    )
