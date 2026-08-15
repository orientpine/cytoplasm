"""Substitution of resolved personal values into one external-write payload."""

from __future__ import annotations

from collections.abc import Mapping
from typing import assert_never

from .contracts import JsonValue, PreflightDecision


def normalized_payload(
    payload: Mapping[str, JsonValue],
    decision: PreflightDecision,
) -> dict[str, JsonValue]:
    """Replace every detected surface with the value the preflight selected."""

    replacements = {
        entity.surface: selected.normalized_value
        for entity in decision.request.entities
        for selected in decision.selected
        if selected.mention_id == entity.mention_id
    }
    return {key: _replace_value(value, replacements) for key, value in payload.items()}


def _replace_value(value: JsonValue, replacements: Mapping[str, str]) -> JsonValue:
    match value:
        case str() as text:
            return _replace_text(text, replacements)
        case list() as values:
            return [_replace_value(item, replacements) for item in values]
        case dict() as values:
            return {key: _replace_value(item, replacements) for key, item in values.items()}
        case None | bool() | int() | float():
            return value
        case unreachable:
            assert_never(unreachable)


def _replace_text(text: str, replacements: Mapping[str, str]) -> str:
    normalized = text
    for surface, value in sorted(replacements.items(), key=lambda item: (-len(item[0]), item[0])):
        normalized = normalized.replace(surface, value)
    return normalized
