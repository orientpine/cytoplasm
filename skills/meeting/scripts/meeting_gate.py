"""Deterministic pre-LLM sensitivity gate (constraint 6).

Loads keyword/regex rules from configs/sensitivity-rules.yaml and evaluates
the EXTRACTED text before any LLM routing decision. No LLM participates.
PyYAML is used when present; a strict fallback parser covers the sandbox.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TagRule:
    """One tag's deterministic keyword + regex rule set."""

    tag: str
    keywords: tuple[str, ...]
    patterns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GateResult:
    """Gate verdict. `matched` is private-log-only, never for repo/channels."""

    sensitive: bool
    tags: tuple[str, ...]
    matched: tuple[str, ...]


def _parse_rules_fallback(raw: str) -> dict:
    """Minimal indentation parser for this file's fixed schema (no PyYAML)."""
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
            key = content[:-1]
            current_list = tags[current_tag].setdefault(key, [])
        elif content.startswith("- ") and current_list is not None:
            value = content[2:].strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1].replace("\\\\", "\\")
            current_list.append(value)
    return {"version": 1, "tags": tags}


def load_rules(path: Path) -> tuple[TagRule, ...]:
    """Load tag rules; fail closed on malformed schema (raise, never guess)."""
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # noqa: PLC0415

        data = yaml.safe_load(raw)
    except ModuleNotFoundError:
        data = _parse_rules_fallback(raw)
    if not isinstance(data, dict) or not isinstance(data.get("tags"), dict):
        raise ValueError(f"malformed sensitivity rules: {path}")
    rules = []
    for tag, spec in data["tags"].items():
        keywords = tuple(str(k) for k in (spec or {}).get("keywords") or ())
        patterns = tuple(str(p) for p in (spec or {}).get("patterns") or ())
        for pattern in patterns:
            re.compile(pattern)
        rules.append(TagRule(tag=str(tag), keywords=keywords, patterns=patterns))
    return tuple(rules)


def evaluate(text: str, rules: tuple[TagRule, ...]) -> GateResult:
    """Deterministically match rules against text (case-insensitive keywords)."""
    lowered = text.lower()
    hit_tags: list[str] = []
    matched: list[str] = []
    for rule in rules:
        rule_hits = [kw for kw in rule.keywords if kw.lower() in lowered]
        rule_hits += [
            pattern for pattern in rule.patterns if re.search(pattern, text) is not None
        ]
        if rule_hits:
            hit_tags.append(rule.tag)
            matched.extend(rule_hits)
    return GateResult(
        sensitive="patent-sensitive" in hit_tags,
        tags=tuple(hit_tags),
        matched=tuple(matched),
    )


def sanitize_public_text(text: str, rules: tuple[TagRule, ...]) -> str:
    """Best-effort scrub of rule terms from text destined for public surfaces."""
    scrubbed = text
    for rule in rules:
        for keyword in rule.keywords:
            scrubbed = re.sub(re.escape(keyword), "▩▩", scrubbed, flags=re.IGNORECASE)
        for pattern in rule.patterns:
            scrubbed = re.sub(pattern, "▩▩", scrubbed)
    return scrubbed
