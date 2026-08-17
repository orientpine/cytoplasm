"""신뢰 경계의 JSON 입출력 — 원장 판단 로직과 분리한다.

`ledger` 는 "무엇이 바뀌었나"를 판단하고, 여기는 "그것을 어떻게 안전하게 읽고 쓰나"만 맡는다.
읽기는 손상 시 추측 대신 멈추고(fail-closed), 쓰기는 임시파일 + os.replace 로 원자적이며 0600 이다.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol, TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
class JsonLoader(Protocol):
    def __call__(self, document: str) -> JsonValue: ...


_JSON_LOADS: JsonLoader = json.loads
class AuditError(RuntimeError):
    pass


def _mapping(value: JsonValue, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise AuditError(f"{label} must be a JSON object")
    return value


def _read_json(path: Path, label: str) -> JsonValue:
    try:
        document = path.read_text(encoding="utf-8")
        return _JSON_LOADS(document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuditError(f"cannot read trusted {label}: {path}") from error


def _atomic_write(path: Path, document: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary_path = Path(handle.name)
            _ = handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as error:
        raise AuditError(f"cannot atomically write private audit file: {path}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
