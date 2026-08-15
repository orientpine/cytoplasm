"""Typed, loud JSON field extraction for the entity-preflight fixtures.

Every accessor names the offending file and key when a fixture is malformed, so
a bad drop-in file reports what is wrong instead of failing somewhere deep in
the contract constructors.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import TypeVar

_EnumT = TypeVar("_EnumT", bound=Enum)

JsonObject = Mapping[str, object]


class FixtureError(ValueError):
    """A fixture file is missing, malformed, or self-contradictory."""


def as_object(value: object, path: Path, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise FixtureError(f"{path.name}: {label} must be a JSON object")
    return {str(name): item for name, item in value.items()}


def field(payload: JsonObject, key: str, path: Path) -> object:
    if key not in payload:
        raise FixtureError(f"{path.name}: missing required key '{key}'")
    return payload[key]


def text(payload: JsonObject, key: str, path: Path) -> str:
    value = field(payload, key, path)
    if not isinstance(value, str) or not value:
        raise FixtureError(f"{path.name}: '{key}' must be a non-empty string")
    return value


def optional_text(payload: JsonObject, key: str, path: Path) -> str | None:
    if field(payload, key, path) is None:
        return None
    return text(payload, key, path)


def flag(payload: JsonObject, key: str, path: Path) -> bool:
    value = field(payload, key, path)
    if not isinstance(value, bool):
        raise FixtureError(f"{path.name}: '{key}' must be a boolean")
    return value


def number(payload: JsonObject, key: str, path: Path) -> float:
    value = field(payload, key, path)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FixtureError(f"{path.name}: '{key}' must be a number")
    return float(value)


def mapping(payload: JsonObject, key: str, path: Path) -> JsonObject:
    return as_object(field(payload, key, path), path, f"'{key}'")


def rows(payload: JsonObject, key: str, path: Path) -> tuple[JsonObject, ...]:
    value = field(payload, key, path)
    if not isinstance(value, list):
        raise FixtureError(f"{path.name}: '{key}' must be a list of objects")
    return tuple(as_object(row, path, f"every '{key}' entry") for row in value)


def strings(payload: JsonObject, key: str, path: Path) -> tuple[str, ...]:
    value = field(payload, key, path)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise FixtureError(f"{path.name}: '{key}' must be a list of strings")
    return tuple(str(item) for item in value)


def enum_value(kind: type[_EnumT], value: str, path: Path) -> _EnumT:
    try:
        return kind(value)
    except ValueError as error:
        raise FixtureError(f"{path.name}: unknown {kind.__name__} '{value}'") from error
