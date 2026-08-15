from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, TypeAlias

from .registry import RotationError

FileWriter: TypeAlias = Callable[[Path, bytes], None]
FileDeleter: TypeAlias = Callable[[Path], None]


@dataclass(frozen=True, slots=True)
class EnvDocument:
    content: str
    values: Mapping[str, str]


def atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    descriptor = -1
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def parse_env(content: str) -> EnvDocument:
    values: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            raise RotationError("verifier file has malformed or duplicate key data")
        values[key] = value
    return EnvDocument(content, values)


def required_value(document: EnvDocument, key: str, label: str) -> str:
    value = document.values.get(key)
    if not value:
        raise RotationError(f"required {label} is missing")
    return value


def note_password(content: str) -> str:
    matches = [
        line.rstrip("\r\n")[len("password:") :].lstrip()
        for line in content.splitlines(keepends=True)
        if line.rstrip("\r\n").startswith("password:")
    ]
    if len(matches) != 1 or not matches[0]:
        raise RotationError("credential note must contain exactly one non-empty password line")
    return matches[0]


def replace_env_value(content: str, expected_key: str, replacement: str) -> str:
    replaced = 0
    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        key, separator, _value = body.partition("=")
        if separator and key == expected_key:
            lines.append(f"{key}={replacement}{ending}")
            replaced += 1
        else:
            lines.append(line)
    if replaced != 1:
        raise RotationError("verifier file changed during rotation preparation")
    return "".join(lines)


def rewrite_note(content: str, password: str, timestamp: str) -> str:
    """Retain all non-password/non-rotated lines byte-for-byte."""
    rewritten: list[str] = []
    password_lines = 0
    for line in content.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        if body.startswith("password:"):
            # Bound outside the f-string: a backslash inside a replacement field is
            # PEP 701 syntax, which the 3.11 interpreters on the nodes reject.
            line_ending = ending or "\n"
            rewritten.append(f"password: {password}{line_ending}")
            password_lines += 1
        elif not body.startswith("rotated:"):
            rewritten.append(line)
    if password_lines != 1:
        raise RotationError("credential note changed during rotation preparation")
    prefix = "".join(rewritten)
    if prefix and not prefix.endswith(("\n", "\r")):
        prefix += "\n"
    return f"{prefix}rotated: {timestamp}\n"


def take_backups(paths: tuple[Path, Path], writer: FileWriter, deleter: FileDeleter) -> tuple[Path, Path]:
    backups: list[Path] = []
    try:
        for path in paths:
            backup = path.with_name(f".{path.name}.{secrets.token_hex(8)}.backup")
            writer(backup, path.read_bytes())
            backups.append(backup)
    except OSError:
        delete_backups(tuple(backups), deleter)
        raise RotationError("could not create credential backups") from None
    return backups[0], backups[1]


def delete_backups(backups: tuple[Path, ...], deleter: FileDeleter) -> None:
    for backup in backups:
        deleter(backup)
