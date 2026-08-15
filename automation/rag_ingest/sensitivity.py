"""Deterministic sensitivity rules for ingest-time document tagging."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias

_EXPECTED_VERSION: Final = "1"
_PATENT_TAG: Final = "patent-sensitive"
_LIST_NAMES: Final = frozenset({"keywords", "patterns"})


@dataclass(frozen=True, slots=True)
class SensitivityRule:
    """One deterministic tag rule loaded from the flat sensitivity YAML subset."""

    tag: str
    keywords: tuple[str, ...]
    patterns: tuple[re.Pattern[str], ...]


@dataclass(frozen=True, slots=True)
class SensitivityRulesError(Exception):
    """Sensitivity rules could not be parsed safely, so ingestion must stop."""

    path: Path
    reason: str

    def __str__(self) -> str:
        return f"malformed sensitivity rules at {self.path}: {self.reason}"


SensitivityRules: TypeAlias = tuple[SensitivityRule, ...]


def _strip_comment(line: str) -> str:
    if line.lstrip().startswith("#"):
        return ""
    return line.split("#", 1)[0].rstrip()


def _parse_list_value(path: Path, value: str) -> str:
    if not value:
        raise SensitivityRulesError(path, "empty list item")
    if value[0] in {'"', "'"}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as error:
            raise SensitivityRulesError(path, f"invalid quoted string {value!r}") from error
        if not isinstance(parsed, str):
            raise SensitivityRulesError(path, f"non-string list item {value!r}")
        return parsed
    return value


def _compile_pattern(path: Path, raw_pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(raw_pattern)
    except re.error as error:
        raise SensitivityRulesError(path, f"invalid regex {raw_pattern!r}") from error


def load_rules(path: Path) -> SensitivityRules:
    """Load the stable flat keywords/patterns subset of sensitivity-rules.yaml."""
    raw = path.read_text(encoding="utf-8")
    version = ""
    tags: dict[str, dict[str, list[str]]] = {}
    current_tag = ""
    current_list = ""
    saw_tags_header = False
    for raw_line in raw.splitlines():
        stripped = _strip_comment(raw_line)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip())
        content = stripped.strip()
        if indent == 0 and content.startswith("version:"):
            version = content.partition(":")[2].strip()
            continue
        if indent == 0 and content == "tags:":
            saw_tags_header = True
            continue
        if indent == 2 and content.endswith(":") and saw_tags_header:
            current_tag = content[:-1]
            tags[current_tag] = {"keywords": [], "patterns": []}
            current_list = ""
            continue
        if indent == 4 and content.endswith(":") and current_tag:
            list_name = content[:-1]
            if list_name not in _LIST_NAMES:
                raise SensitivityRulesError(path, f"unknown rule list {list_name!r}")
            current_list = list_name
            continue
        if indent == 6 and content.startswith("- ") and current_tag and current_list:
            tags[current_tag][current_list].append(_parse_list_value(path, content[2:].strip()))
            continue
        raise SensitivityRulesError(path, f"unexpected line {content!r}")
    if version != _EXPECTED_VERSION:
        raise SensitivityRulesError(path, f"unsupported version {version!r}")
    if _PATENT_TAG not in tags:
        raise SensitivityRulesError(path, f"missing tag {_PATENT_TAG!r}")
    rules: list[SensitivityRule] = []
    for tag, spec in tags.items():
        keywords = tuple(spec["keywords"])
        patterns = tuple(_compile_pattern(path, pattern) for pattern in spec["patterns"])
        if not keywords and not patterns:
            raise SensitivityRulesError(path, f"empty rule for tag {tag!r}")
        rules.append(SensitivityRule(tag=tag, keywords=keywords, patterns=patterns))
    return tuple(rules)


def classify(text: str, rules: SensitivityRules) -> frozenset[str]:
    """Return tags whose keyword substring or regex pattern matches the text."""
    lowered = text.lower()
    tags: set[str] = set()
    for rule in rules:
        keyword_hit = any(keyword.lower() in lowered for keyword in rule.keywords)
        pattern_hit = any(pattern.search(text) is not None for pattern in rule.patterns)
        if keyword_hit or pattern_hit:
            tags.add(rule.tag)
    return frozenset(tags)
