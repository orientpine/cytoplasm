from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TagRule:
    tag: str
    keywords: tuple[str, ...]
    patterns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SensitivityVerdict:
    sensitive: bool
    tags: tuple[str, ...]


def _parse_rules_fallback(raw: str) -> dict[str, dict[str, dict[str, list[str]]] | int]:
    tags: dict[str, dict[str, list[str]]] = {}
    current_tag: str | None = None
    current_list: list[str] | None = None
    for line in raw.splitlines():
        stripped = line.split("#", 1)[0].rstrip() if not line.lstrip().startswith("#") else ""
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip())
        content = stripped.strip()
        if indent == 2 and content.endswith(":"):
            current_tag = content[:-1]
            tags[current_tag] = {"keywords": [], "patterns": []}
            current_list = None
        elif indent == 4 and content.endswith(":") and current_tag:
            current_list = tags[current_tag].setdefault(content[:-1], [])
        elif content.startswith("- ") and current_list is not None:
            value = content[2:].strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1].replace("\\\\", "\\")
            current_list.append(value)
    return {"version": 1, "tags": tags}


def load_rules(path: Path) -> tuple[TagRule, ...]:
    data = _parse_rules_fallback(path.read_text(encoding="utf-8"))
    tags_data = data["tags"]
    if not isinstance(tags_data, dict):
        raise ValueError(f"malformed sensitivity rules: {path}")
    rules: list[TagRule] = []
    for tag, spec in tags_data.items():
        keywords = tuple(spec.get("keywords", ()))
        patterns = tuple(spec.get("patterns", ()))
        for pattern in patterns:
            compiled = re.compile(pattern)
            if compiled.pattern != pattern:
                raise ValueError(f"malformed sensitivity pattern: {pattern}")
        rules.append(TagRule(tag, keywords, patterns))
    return tuple(rules)


def evaluate(text: str, rules: tuple[TagRule, ...]) -> SensitivityVerdict:
    lowered = text.lower()
    hit_tags: list[str] = []
    for rule in rules:
        keyword_hit = any(keyword.lower() in lowered for keyword in rule.keywords)
        pattern_hit = any(re.search(pattern, text) is not None for pattern in rule.patterns)
        if keyword_hit or pattern_hit:
            hit_tags.append(rule.tag)
    return SensitivityVerdict(
        sensitive="patent-sensitive" in hit_tags,
        tags=tuple(hit_tags),
    )
