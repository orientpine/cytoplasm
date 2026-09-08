"""Runtime configuration and safety helpers for the Plaud cron wrapper."""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
from pathlib import Path
from typing import IO, Final, Protocol, TypeAlias
from zoneinfo import ZoneInfo

from .lifelog_fields import note_timezone as resolve_note_timezone

__all__ = [
    "ENV_SECRETS",
    "INTEROP_CONFIG",
    "LOCK_PATH",
    "STATE_DIR",
    "STATE_PATH",
    "JsonLoader",
    "WatchError",
    "acquire_single_instance_lock",
    "env_int",
    "load_env_secrets",
    "masked_error",
    "note_timezone",
    "owner_id",
]

ENV_SECRETS: Final = Path.home() / ".env.secrets"
INTEROP_CONFIG: Final = Path.home() / ".hermes" / "interop" / "config.json"
STATE_DIR: Final = Path.home() / ".hermes" / "plaud-sync"
STATE_PATH: Final = STATE_DIR / "state.json"
LOCK_PATH: Final = STATE_DIR / "watch.lock"
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

_LONG_DIGITS = re.compile(r"\d{5,}")
_SECRET_VALUE = re.compile(r"(?i)(token|secret|password|key)=[^\s]+")


class JsonLoader(Protocol):
    def __call__(self, s: str) -> JsonValue: ...


_JSON_LOADS: JsonLoader = json.loads


class WatchError(RuntimeError):
    """Node configuration is insufficient for a fail-closed sync tick."""


def load_env_secrets(path: Path = ENV_SECRETS) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        key, separator, value = raw_line.strip().partition("=")
        if separator and key and not key.startswith("#") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def owner_id(path: Path = INTEROP_CONFIG) -> str:
    try:
        payload = _JSON_LOADS(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WatchError("interop owner configuration is unavailable") from error
    if not isinstance(payload, dict):
        raise WatchError("interop owner configuration is malformed")
    owner_id = payload.get("owner_id")
    if not isinstance(owner_id, str) or not owner_id:
        raise WatchError("interop owner configuration has no owner id")
    return owner_id


def acquire_single_instance_lock(lock_path: Path = LOCK_PATH) -> IO[str] | None:
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def note_timezone() -> ZoneInfo:
    """Return the configured note zone and print an invalid-zone warning."""
    zone, warning = resolve_note_timezone(os.environ)
    if warning:
        print(f"plaud-sync: {warning}", file=sys.stderr)
    return zone


def masked_error(error: Exception) -> str:
    secret_safe = _SECRET_VALUE.sub(r"\1=[MASKED]", str(error))
    return _LONG_DIGITS.sub("[MASKED-NUM]", secret_safe)[:300]
