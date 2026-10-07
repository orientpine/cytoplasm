"""Immutable contract models and the small JSON codec their call sites need.

The engine carried pydantic only for "frozen + type hints + a little JSON".
This repository's main tree is standard-library only, so that base lives here
instead, keeping the public surface (`model_validate`, `model_validate_json`,
`model_dump`) byte-compatible with what every call site already writes.
"""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any, Union, get_args, get_origin

_NONE = type(None)


class FrozenModel:
    """Base for the engine's immutable contracts; subclasses are frozen dataclasses."""

    @classmethod
    def model_validate(cls, data: object) -> Any:
        if isinstance(data, cls):
            return data
        if not isinstance(data, Mapping):
            raise ValueError(f"{cls.__name__}: expected an object, got {type(data).__name__}")
        hints = typing.get_type_hints(cls)
        values: dict[str, object] = {}
        missing: list[str] = []
        for spec in dataclasses.fields(cls):
            if spec.name in data:
                values[spec.name] = _coerce(
                    data[spec.name], hints[spec.name], f"{cls.__name__}.{spec.name}"
                )
            elif (
                spec.default is dataclasses.MISSING
                and spec.default_factory is dataclasses.MISSING
            ):
                missing.append(spec.name)
        if missing:
            raise ValueError(f"{cls.__name__}: missing required field(s): {', '.join(missing)}")
        return cls(**values)

    @classmethod
    def model_validate_json(cls, text: str | bytes) -> Any:
        return cls.model_validate(json.loads(text))

    def model_copy(self, *, update: Mapping[str, Any] | None = None) -> Any:
        return dataclasses.replace(self, **dict(update or {}))

    def model_dump_json(self, *, exclude_none: bool = False, **kwargs: Any) -> str:
        return json.dumps(self.model_dump(exclude_none=exclude_none), **kwargs)

    def model_dump(self, *, mode: str = "json", exclude_none: bool = False) -> dict[str, Any]:
        _ = mode
        dumped = _dump(self, exclude_none=exclude_none)
        return typing.cast("dict[str, Any]", dumped)


def _sequence(value: object, path: str) -> list[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{path}: expected a sequence, got {type(value).__name__}")
    return list(value)


def _coerce(value: object, annotation: Any, path: str) -> Any:
    if annotation is Any:
        return value

    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        members = get_args(annotation)
        if value is None and _NONE in members:
            return None
        reasons: list[str] = []
        for member in members:
            if member is _NONE:
                continue
            try:
                return _coerce(value, member, path)
            except (TypeError, ValueError) as error:
                reasons.append(str(error))
        raise ValueError(f"{path}: no declared type accepted {value!r} ({'; '.join(reasons)})")

    if origin is list:
        (item,) = get_args(annotation) or (Any,)
        return [_coerce(v, item, f"{path}[{i}]") for i, v in enumerate(_sequence(value, path))]

    if origin is tuple:
        members = get_args(annotation)
        items = _sequence(value, path)
        if len(members) == 2 and members[1] is Ellipsis:
            return tuple(_coerce(v, members[0], f"{path}[{i}]") for i, v in enumerate(items))
        if len(members) != len(items):
            raise ValueError(f"{path}: expected {len(members)} items, got {len(items)}")
        return tuple(
            _coerce(v, member, f"{path}[{i}]")
            for i, (v, member) in enumerate(zip(items, members, strict=True))
        )

    if origin is dict:
        key_type, value_type = get_args(annotation) or (Any, Any)
        if not isinstance(value, Mapping):
            raise ValueError(f"{path}: expected an object, got {type(value).__name__}")
        return {
            _coerce(k, key_type, path): _coerce(v, value_type, f"{path}.{k}")
            for k, v in value.items()
        }

    if origin is typing.Literal:
        allowed = get_args(annotation)
        if value not in allowed:
            raise ValueError(f"{path}: {value!r} is not one of {allowed!r}")
        return value

    if isinstance(annotation, type):
        if issubclass(annotation, Enum):
            return annotation(value)
        if issubclass(annotation, FrozenModel):
            return annotation.model_validate(value)
        if annotation is datetime:
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                return datetime.fromisoformat(value)
            raise ValueError(f"{path}: expected a datetime, got {type(value).__name__}")
        if annotation is bool:
            if isinstance(value, bool):
                return value
            raise ValueError(f"{path}: expected a bool, got {type(value).__name__}")
        if annotation is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{path}: expected an int, got {type(value).__name__}")
            return value
        if annotation is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{path}: expected a number, got {type(value).__name__}")
            return float(value)
        if annotation is str:
            if isinstance(value, str):
                return value
            raise ValueError(f"{path}: expected a str, got {type(value).__name__}")
        if isinstance(value, annotation):
            return value
        raise ValueError(f"{path}: expected {annotation.__name__}, got {type(value).__name__}")

    return value


def _dump(value: object, *, exclude_none: bool) -> object:
    if isinstance(value, FrozenModel):
        dumped: dict[str, object] = {}
        for spec in dataclasses.fields(value):
            item = getattr(value, spec.name)
            if exclude_none and item is None:
                continue
            dumped[spec.name] = _dump(item, exclude_none=exclude_none)
        return dumped
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {k: _dump(v, exclude_none=exclude_none) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(v, exclude_none=exclude_none) for v in value]
    return value


__all__ = ["FrozenModel"]
