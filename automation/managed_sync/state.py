"""Durable managed-sync state kept outside source checkouts."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Final, TypeAlias

_SCHEMA_VERSION: Final = 1
_STATE_KEYS: Final = frozenset({"schema_version", "skills"})
_SKILL_KEYS: Final = frozenset(
    {"highest_sequence", "last_verified_digest", "activated_digest", "revoked_digests"},
)

_JsonValue: TypeAlias = (
    str | int | float | bool | None | list["_JsonValue"] | dict[str, "_JsonValue"]
)


_JSON_LOADS: Final[Callable[..., _JsonValue]] = json.loads

class StateError(Exception):
    """Raised when managed-sync state cannot be read or written safely."""


@dataclass(frozen=True, slots=True)
class SkillState:
    """Durable state for one managed skill."""

    highest_sequence: int = 0
    last_verified_digest: str | None = None
    activated_digest: str | None = None
    revoked_digests: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ManagedSyncState:
    """Immutable snapshot of all managed-sync runtime state."""

    schema_version: int = _SCHEMA_VERSION
    skills: Mapping[str, SkillState] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.schema_version != _SCHEMA_VERSION:
            raise StateError(f"unsupported managed-sync state schema: {self.schema_version}")
        object.__setattr__(self, "skills", MappingProxyType(dict(self.skills)))

    def skill(self, name: str) -> SkillState:
        """Return a skill record, defaulting absent skills to an empty record."""
        return self.skills.get(name, _EMPTY_SKILL)


_EMPTY_SKILL: Final = SkillState()


def _with_skill(state: ManagedSyncState, name: str, skill: SkillState) -> ManagedSyncState:
    skills = dict(state.skills)
    skills[name] = skill
    return ManagedSyncState(schema_version=state.schema_version, skills=skills)


def record_verified(
    state: ManagedSyncState,
    skill: str,
    sequence: int,
    digest: str,
) -> ManagedSyncState:
    """Record a verified release after enforcing strictly increasing sequence."""
    if isinstance(sequence, bool):
        raise StateError(f"managed-sync sequence must be an integer: {sequence!r}")
    current = state.skill(skill)
    if sequence <= current.highest_sequence:
        raise StateError(f"non-monotonic sequence for {skill}: {sequence} <= {current.highest_sequence}")
    return _with_skill(
        state,
        skill,
        replace(current, highest_sequence=sequence, last_verified_digest=digest),
    )


def record_activated(
    state: ManagedSyncState, skill: str, digest: str | None
) -> ManagedSyncState:
    """Record the digest currently activated for a skill."""
    return _with_skill(state, skill, replace(state.skill(skill), activated_digest=digest))


def add_revoked(
    state: ManagedSyncState,
    skill: str,
    digests: Iterable[str],
) -> ManagedSyncState:
    """Add revoked digests as a deduplicated, sorted tuple."""
    current = state.skill(skill)
    revoked = set(current.revoked_digests)
    revoked.update(digests)
    return _with_skill(state, skill, replace(current, revoked_digests=tuple(sorted(revoked))))


def _skill_payload(skill: SkillState) -> dict[str, _JsonValue]:
    return {
        "highest_sequence": skill.highest_sequence,
        "last_verified_digest": skill.last_verified_digest,
        "activated_digest": skill.activated_digest,
        "revoked_digests": list(skill.revoked_digests),
    }


def _state_payload(state: ManagedSyncState) -> dict[str, _JsonValue]:
    return {
        "schema_version": state.schema_version,
        "skills": {name: _skill_payload(skill) for name, skill in state.skills.items()},
    }


def _invalid_state(message: str) -> StateError:
    return StateError(f"malformed managed-sync state: {message}")


def _parse_skill(name: str, payload: _JsonValue) -> SkillState:
    if not isinstance(payload, dict) or frozenset(payload) != _SKILL_KEYS:
        raise _invalid_state(f"skill {name!r} has an invalid shape")

    highest_sequence = payload["highest_sequence"]
    if (
        not isinstance(highest_sequence, int)
        or isinstance(highest_sequence, bool)
        or highest_sequence < 0
    ):
        raise _invalid_state(f"skill {name!r} has an invalid highest_sequence")

    last_verified_digest = payload["last_verified_digest"]
    if last_verified_digest is not None and not isinstance(last_verified_digest, str):
        raise _invalid_state(f"skill {name!r} has an invalid last_verified_digest")

    activated_digest = payload["activated_digest"]
    if activated_digest is not None and not isinstance(activated_digest, str):
        raise _invalid_state(f"skill {name!r} has an invalid activated_digest")

    revoked_digests = payload["revoked_digests"]
    if not isinstance(revoked_digests, list):
        raise _invalid_state(f"skill {name!r} has invalid revoked_digests")
    parsed_revoked_digests: list[str] = []
    for digest in revoked_digests:
        if not isinstance(digest, str):
            raise _invalid_state(f"skill {name!r} has invalid revoked_digests")
        parsed_revoked_digests.append(digest)

    return SkillState(
        highest_sequence=highest_sequence,
        last_verified_digest=last_verified_digest,
        activated_digest=activated_digest,
        revoked_digests=tuple(parsed_revoked_digests),
    )


def _parse_state(payload: _JsonValue) -> ManagedSyncState:
    if not isinstance(payload, dict) or frozenset(payload) != _STATE_KEYS:
        raise _invalid_state("top-level shape")

    schema_version = payload["schema_version"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != _SCHEMA_VERSION
    ):
        raise _invalid_state("schema_version")

    skills_payload = payload["skills"]
    if not isinstance(skills_payload, dict):
        raise _invalid_state("skills")

    skills: dict[str, SkillState] = {}
    for name, skill_payload in skills_payload.items():
        skills[name] = _parse_skill(name, skill_payload)
    return ManagedSyncState(schema_version=schema_version, skills=skills)


def load_state(path: Path) -> ManagedSyncState:
    """Load state, treating only a missing file as the empty default."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ManagedSyncState()
    except UnicodeDecodeError as error:
        raise StateError(f"managed-sync state is not UTF-8: {path}") from error
    except OSError as error:
        raise StateError(f"cannot read managed-sync state: {path}") from error

    try:
        payload = _JSON_LOADS(text)
    except json.JSONDecodeError as error:
        raise StateError(f"managed-sync state is not valid JSON: {path}") from error
    return _parse_state(payload)


def _refuse_checkout_path(path: Path) -> None:
    try:
        parent = path.resolve().parent
    except OSError as error:
        raise StateError(f"cannot resolve managed-sync state path: {path}") from error
    for candidate in (parent, *parent.parents):
        marker = candidate / ".git"
        if marker.is_dir() or marker.is_file():
            raise StateError(f"managed-sync runtime state must be outside a git checkout: {path}")


def save_state(path: Path, state: ManagedSyncState) -> None:
    """Atomically save state with mode 0600, refusing git checkout paths."""
    _refuse_checkout_path(path)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        serialized = json.dumps(_state_payload(state), sort_keys=True, separators=(",", ":")) + "\n"
        temporary_path: Path | None = None
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
                _ = os.fsync(temporary.fileno())
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
        raise StateError(f"cannot save managed-sync state: {path}") from error
