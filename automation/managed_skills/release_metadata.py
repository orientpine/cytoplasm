"""Strict parser for the release metadata already consumed by ``publish_cli``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeAlias, TypeGuard

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class _JsonLoader(Protocol):
    def __call__(self, raw: str, /) -> JsonValue: ...


_JSON_LOADS: _JsonLoader = json.loads


class ReleaseMetadataError(Exception):
    """Release metadata cannot safely populate a managed manifest."""


@dataclass(frozen=True, slots=True)
class ReleaseMetadata:
    """The human-authored fields shared by local and submitted releases."""

    compatibility: str
    breaking: bool
    revoked_digests: tuple[str, ...]
    changelog: str
    migration: str | None


def _is_json_object(value: JsonValue) -> TypeGuard[dict[str, JsonValue]]:
    return isinstance(value, dict)


def load_release_metadata(path: Path) -> ReleaseMetadata:
    """Parse the existing changelog JSON shape without accepting unknown fields."""
    try:
        raw = _JSON_LOADS(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseMetadataError(f"release metadata is unreadable JSON: {path}") from error
    if not _is_json_object(raw):
        raise ReleaseMetadataError("release metadata must contain a JSON object")
    allowed = {"changelog", "breaking", "compatibility", "migration", "revoked_digests"}
    missing = {"changelog", "breaking", "compatibility"} - set(raw)
    if set(raw) - allowed or missing:
        raise ReleaseMetadataError("release metadata has unknown or missing fields")
    changelog = raw["changelog"]
    breaking = raw["breaking"]
    compatibility = raw["compatibility"]
    migration = raw.get("migration")
    revoked = raw.get("revoked_digests", [])
    if not isinstance(changelog, str) or not isinstance(compatibility, str):
        raise ReleaseMetadataError("changelog and compatibility must be strings")
    if not isinstance(breaking, bool):
        raise ReleaseMetadataError("breaking must be a boolean")
    if migration is not None and not isinstance(migration, str):
        raise ReleaseMetadataError("migration must be a string or null")
    if not isinstance(revoked, list) or any(not isinstance(value, str) for value in revoked):
        raise ReleaseMetadataError("revoked_digests must be a string list")
    revoked_digests: list[str] = []
    for value in revoked:
        if isinstance(value, str):
            revoked_digests.append(value)
    return ReleaseMetadata(
        compatibility=compatibility,
        breaking=breaking,
        revoked_digests=tuple(revoked_digests),
        changelog=changelog,
        migration=migration,
    )
