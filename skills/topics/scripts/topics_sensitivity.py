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
class GateResult:
    sensitive: bool
    tags: tuple[str, ...]


class RuleLoadError(RuntimeError):
    pass


def _fallback_rules(raw: str) -> dict[str, dict[str, dict[str, list[str]]]]:
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
            scalar = value[2:].strip()
            if scalar.startswith('"') and scalar.endswith('"'):
                scalar = scalar[1:-1].replace("\\\\", "\\")
            current_list.append(scalar)
    return {"tags": tags}


def load_rules(path: Path) -> tuple[TagRule, ...]:
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml

        parsed = yaml.safe_load(raw)
    except ModuleNotFoundError:
        parsed = _fallback_rules(raw)
    if not isinstance(parsed, dict):
        raise RuleLoadError(f"invalid sensitivity rules: {path}")
    tags = parsed.get("tags")
    if not isinstance(tags, dict):
        raise RuleLoadError(f"missing tags in sensitivity rules: {path}")
    rules: list[TagRule] = []
    for tag, spec in tags.items():
        if not isinstance(spec, dict):
            raise RuleLoadError(f"invalid rule spec: {tag}")
        keywords = tuple(str(value) for value in spec.get("keywords", []))
        patterns = tuple(str(value) for value in spec.get("patterns", []))
        for pattern in patterns:
            re.compile(pattern)
        rules.append(TagRule(str(tag), keywords, patterns))
    return tuple(rules)


def evaluate(topic: str, rules: tuple[TagRule, ...]) -> GateResult:
    lowered = topic.lower()
    tags = tuple(
        rule.tag
        for rule in rules
        if any(keyword.lower() in lowered for keyword in rule.keywords)
        or any(re.search(pattern, topic) for pattern in rule.patterns)
    )
    return GateResult(sensitive="patent-sensitive" in tags, tags=tags)
