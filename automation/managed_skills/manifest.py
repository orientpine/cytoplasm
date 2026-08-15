"""Managed skill release manifest schema v1 (fail-closed parse + canonical digest).

Single source of truth for the managed-skill channel naming constants and the
release manifest wire format. MS-N1 / MS-P2 / MS-S1 import ``MANAGED_PREFIX``,
``MAX_SKILL_NAME``, ``parse_manifest`` and ``manifest_digest`` from here —
keep those names and signatures stable.

True version identity is the source digest (``skill_sha256``, computed by
``automation.skill_review.skill_digest``); ``release_sequence`` provides
ordering and replay protection only. ``source_commit`` is provenance metadata
(40-hex or null) and is never compared against anything.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Final

MANAGED_PREFIX: Final = "managed-"
MAX_SKILL_NAME: Final = 41
SCHEMA_VERSION: Final = 1

_SKILL_NAME: Final = re.compile(r"\Amanaged-[a-z0-9][a-z0-9-]*\Z")
_DIGEST: Final = re.compile(r"\A[0-9a-f]{64}\Z")
_COMMIT: Final = re.compile(r"\A[0-9a-f]{40}\Z")

_REQUIRED_FIELDS: Final = (
    "schema_version",
    "publisher",
    "skill",
    "release_sequence",
    "source_commit",
    "skill_sha256",
    "previous_sha256",
    "compatibility",
    "breaking",
    "revoked_digests",
    "changelog",
)
_ALLOWED_FIELDS: Final = frozenset((*_REQUIRED_FIELDS, "migration"))


class ManifestError(Exception):
    """A managed-skill release manifest failed fail-closed validation."""


@dataclass(frozen=True, slots=True)
class ManagedManifest:
    """One immutable, fully validated managed-skill release manifest."""

    schema_version: int
    publisher: str
    skill: str
    release_sequence: int
    source_commit: str | None
    skill_sha256: str
    previous_sha256: str | None
    compatibility: str
    breaking: bool
    revoked_digests: tuple[str, ...]
    changelog: str
    migration: str | None


def _schema_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value != SCHEMA_VERSION:
        raise ManifestError(f"schema_version must be the int {SCHEMA_VERSION}")
    return value


def _string(field: str, value: object) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{field} must be a string")
    return value


def _non_empty_string(field: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{field} must be a non-empty string")
    return value


def _skill_name(value: object) -> str:
    name = _non_empty_string("skill", value)
    if not _SKILL_NAME.fullmatch(name):
        raise ManifestError(
            f"skill must match ^managed-[a-z0-9][a-z0-9-]*$ (got {name!r})"
        )
    if len(name) > MAX_SKILL_NAME:
        raise ManifestError(
            f"skill must be at most {MAX_SKILL_NAME} characters (got {len(name)})"
        )
    return name


def _positive_int(field: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ManifestError(f"{field} must be a positive integer (>= 1)")
    return value


def _digest(field: str, value: object) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ManifestError(f"{field} must be a 64-character lowercase hex sha256")
    return value


def _digest_or_none(field: str, value: object) -> str | None:
    if value is None:
        return None
    return _digest(field, value)


def _commit_or_none(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _COMMIT.fullmatch(value):
        raise ManifestError("source_commit must be a 40-character lowercase hex commit or null")
    return value


def _bool(field: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise ManifestError(f"{field} must be a boolean")
    return value


def _revoked_digests(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ManifestError("revoked_digests must be a list of sha256 digests")
    return tuple(_digest(f"revoked_digests[{index}]", entry) for index, entry in enumerate(value))


def _migration(value: object, breaking: bool) -> str | None:
    if breaking:
        if not isinstance(value, str) or not value:
            raise ManifestError(
                "migration must be a non-empty string when breaking is true"
            )
        return value
    if value is None:
        return None
    return _string("migration", value)


def parse_manifest(text: str) -> ManagedManifest:
    """Parse a JSON manifest string, rejecting anything invalid (fail-closed)."""
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise ManifestError(f"manifest is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ManifestError("manifest must be a JSON object")
    unknown = sorted(set(raw) - _ALLOWED_FIELDS)
    if unknown:
        raise ManifestError(f"unknown manifest fields: {', '.join(unknown)}")
    missing = [field for field in _REQUIRED_FIELDS if field not in raw]
    if missing:
        raise ManifestError(f"missing required manifest fields: {', '.join(missing)}")
    breaking = _bool("breaking", raw["breaking"])
    return ManagedManifest(
        schema_version=_schema_version(raw["schema_version"]),
        publisher=_non_empty_string("publisher", raw["publisher"]),
        skill=_skill_name(raw["skill"]),
        release_sequence=_positive_int("release_sequence", raw["release_sequence"]),
        source_commit=_commit_or_none(raw["source_commit"]),
        skill_sha256=_digest("skill_sha256", raw["skill_sha256"]),
        previous_sha256=_digest_or_none("previous_sha256", raw["previous_sha256"]),
        compatibility=_non_empty_string("compatibility", raw["compatibility"]),
        breaking=breaking,
        revoked_digests=_revoked_digests(raw["revoked_digests"]),
        changelog=_string("changelog", raw["changelog"]),
        migration=_migration(raw.get("migration"), breaking),
    )


def canonical_json(manifest: ManagedManifest) -> str:
    """Byte-stable canonical JSON form (sorted keys, compact separators)."""
    return json.dumps(asdict(manifest), sort_keys=True, separators=(",", ":"))


def manifest_digest(manifest: ManagedManifest) -> str:
    """Reproducible sha256 hex digest of the canonical manifest form."""
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
