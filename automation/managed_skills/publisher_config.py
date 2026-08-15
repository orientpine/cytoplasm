"""Publisher-side runtime identity for the managed-skill channel (fail-closed).

A group administrator's own workstation declares WHO it publishes as. That is
not the roster: the roster is what SUBSCRIBERS read to learn which principal to
trust, and an admin's publish tool must know its own identity locally, before
any roster exists.

There is no default identity and no fallback. An unconfigured install cannot
publish — that is the point: the alternative (a built-in publisher name) is
exactly the hardcoded trust this module removes.

Runtime config lives OUTSIDE the checkout at
``~/.hermes/managed-skills/publisher.json`` (override: ``MANAGED_PUBLISHER_CONFIG``);
the tracked seed is ``configs/managed-publisher.default.json``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias, TypeGuard

from automation.managed_skills.principal import is_publisher_name, is_publisher_principal

CONFIG_ENV: Final = "MANAGED_PUBLISHER_CONFIG"
DEFAULT_CONFIG_PATH: Final = Path("~/.hermes/managed-skills/publisher.json")

_REQUIRED_KEYS: Final = ("publisher", "publisher_principal")

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class PublisherConfigError(Exception):
    """The publisher identity is missing or invalid; publishing must not run."""


@dataclass(frozen=True, slots=True)
class PublisherIdentity:
    """The two names one group administrator publishes releases under."""

    publisher: str
    publisher_principal: str


def config_path() -> Path:
    """Resolve the publisher config path from the environment or the runtime default."""
    raw = os.environ.get(CONFIG_ENV, "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_CONFIG_PATH.expanduser()


def _is_json_object(value: object) -> TypeGuard[dict[str, JsonValue]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _payload(path: Path) -> dict[str, JsonValue]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise PublisherConfigError(f"publisher config not found: {path}") from error
    except OSError as error:
        raise PublisherConfigError(f"cannot read publisher config: {path}") from error
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise PublisherConfigError(f"publisher config is not valid JSON: {path}") from error
    if not _is_json_object(raw):
        raise PublisherConfigError(f"publisher config must be a JSON object: {path}")
    return raw


def load_publisher_identity(path: Path) -> PublisherIdentity:
    """Parse one publisher identity fail-closed, naming the offending key."""
    payload = _payload(path)
    for key in _REQUIRED_KEYS:
        if key not in payload:
            raise PublisherConfigError(f"missing required key: {key}")
    for key in payload:
        if key not in _REQUIRED_KEYS:
            raise PublisherConfigError(f"unknown config key: {key}")
    values: dict[str, str] = {}
    for key in _REQUIRED_KEYS:
        value = payload[key]
        if not isinstance(value, str):
            raise PublisherConfigError(f"invalid value for key: {key}")
        values[key] = value
    if not is_publisher_name(values["publisher"]):
        raise PublisherConfigError("invalid value for key: publisher")
    if not is_publisher_principal(values["publisher_principal"]):
        raise PublisherConfigError("invalid value for key: publisher_principal")
    return PublisherIdentity(
        publisher=values["publisher"],
        publisher_principal=values["publisher_principal"],
    )
