from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol, TypeAlias

from .model import (
    RelocationError,
    RelocationState,
    empty_state,
    parse_state,
    serialize_state,
)

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


class JsonLoader(Protocol):
    def __call__(self, s: str) -> JsonValue: ...


_JSON_LOADS: JsonLoader = json.loads


def load_state(path: Path) -> RelocationState:
    try:
        document = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_state()
    except UnicodeDecodeError as error:
        raise RelocationError(f"relocation state is not UTF-8: {path}") from error
    except OSError as error:
        raise RelocationError(f"cannot read relocation state: {path}") from error

    try:
        raw = _JSON_LOADS(document)
    except json.JSONDecodeError as error:
        raise RelocationError(f"relocation state is not valid JSON: {path}") from error
    return parse_state(raw)


def save_state(path: Path, s: RelocationState) -> None:
    serialized = json.dumps(
        serialize_state(s),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=f".{path.name}.",
                mode="w",
                encoding="utf-8",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                _ = temporary.write(serialized)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    temporary_path = None
    except OSError as error:
        raise RelocationError(f"cannot save relocation state: {path}") from error
