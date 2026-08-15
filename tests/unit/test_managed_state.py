from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from types import MappingProxyType
from typing import TypeAlias

import pytest

from automation.managed_sync.state import (
    ManagedSyncState,
    SkillState,
    StateError,
    add_revoked,
    load_state,
    record_activated,
    record_verified,
    save_state,
)

_JsonValue: TypeAlias = (
    str | int | float | bool | None | list["_JsonValue"] | dict[str, "_JsonValue"]
)
_JSON_LOADS: Callable[..., _JsonValue] = json.loads


def test_missing_state_file_returns_empty_default(tmp_path: Path) -> None:
    # Given: a runtime state path that does not exist.
    path = tmp_path / "state.json"

    # When: the state is loaded.
    state = load_state(path)

    # Then: loading yields the schema-v1 empty state.
    assert state == ManagedSyncState()
    assert state.schema_version == 1
    assert state.skills == {}


def test_save_load_roundtrip_preserves_all_fields(tmp_path: Path) -> None:
    # Given: a state containing every per-skill field.
    digest = "a" * 64
    state = ManagedSyncState(
        skills={
            "managed-example": SkillState(
                highest_sequence=4,
                last_verified_digest=digest,
                activated_digest="b" * 64,
                revoked_digests=("c" * 64, "d" * 64),
            ),
        },
    )

    # When: the state is saved and loaded again.
    path = tmp_path / "state.json"
    _ = save_state(path, state)
    loaded = load_state(path)

    # Then: all persisted fields retain their values.
    assert loaded.schema_version == 1
    assert loaded.skill("managed-example") == state.skill("managed-example")


def test_save_is_atomic_and_sets_private_file_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a good state file and a replacement state.
    path = tmp_path / "state.json"
    original = ManagedSyncState(skills={"managed-example": SkillState(highest_sequence=1)})
    replacement = ManagedSyncState(skills={"managed-example": SkillState(highest_sequence=2)})
    _ = save_state(path, original)
    original_bytes = path.read_bytes()

    # When: replacing the file fails after the temporary file is written.
    def fail_replace(_source: str | os.PathLike[str], _destination: str | os.PathLike[str]) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(StateError):
        save_state(path, replacement)

    # Then: the old file remains intact and private.
    assert path.read_bytes() == original_bytes
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_record_verified_requires_strictly_greater_sequence() -> None:
    # Given: a state whose current sequence is two.
    state = ManagedSyncState(
        skills={"managed-example": SkillState(highest_sequence=2, last_verified_digest="old")},
    )

    # When: an equal or lower sequence is recorded.
    with pytest.raises(StateError):
        _ = record_verified(state, "managed-example", 2, "b" * 64)
    with pytest.raises(StateError):
        _ = record_verified(state, "managed-example", 1, "c" * 64)

    # Then: a strictly greater sequence is accepted without mutating the input.
    updated = record_verified(state, "managed-example", 3, "d" * 64)
    assert state.skill("managed-example").highest_sequence == 2
    assert updated.skill("managed-example") == SkillState(
        highest_sequence=3,
        last_verified_digest="d" * 64,
    )


def test_record_activated_sets_activated_digest() -> None:
    # Given: an empty state.
    state = ManagedSyncState()

    # When: a skill activation is recorded.
    updated = record_activated(state, "managed-example", "a" * 64)

    # Then: only the skill's activated digest is set.
    assert updated.skill("managed-example") == SkillState(activated_digest="a" * 64)
    assert state.skills == {}


def test_add_revoked_unions_and_stably_sorts_digests() -> None:
    # Given: a state with no revocations.
    state = ManagedSyncState()

    # When: revocations are added across multiple transitions.
    first = add_revoked(state, "managed-example", ["c" * 64, "a" * 64, "a" * 64])
    second = add_revoked(first, "managed-example", ["b" * 64, "a" * 64])

    # Then: the union is deduplicated and stably sorted.
    assert second.skill("managed-example").revoked_digests == (
        "a" * 64,
        "b" * 64,
        "c" * 64,
    )
    assert state.skills == {}


def test_save_refuses_path_inside_git_checkout(tmp_path: Path) -> None:
    # Given: a directory tree containing a git checkout marker.
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)

    # When: runtime state is saved below that checkout.
    with pytest.raises(StateError):
        save_state(checkout / "nested" / "state.json", ManagedSyncState())

    # Then: no state file is created.
    assert not (checkout / "nested" / "state.json").exists()


def test_malformed_json_raises_state_error(tmp_path: Path) -> None:
    # Given: a state file containing malformed JSON.
    path = tmp_path / "state.json"
    _ = path.write_text("{not-json", encoding="utf-8")

    # When: the malformed state is loaded.
    # Then: loading fails closed instead of returning an empty state.
    with pytest.raises(StateError):
        _ = load_state(path)


def test_skill_access_is_defaulted_and_read_only() -> None:
    # Given: a state containing one skill.
    state = ManagedSyncState(skills={"managed-example": SkillState(highest_sequence=1)})

    # When: an absent and a present skill are accessed.
    missing = state.skill("missing")
    present = state.skill("managed-example")

    # Then: absent access defaults and the mapping cannot be mutated through the state.
    assert missing == SkillState()
    assert present.highest_sequence == 1
    assert isinstance(state.skills, MappingProxyType)


def test_saved_json_uses_codified_schema(tmp_path: Path) -> None:
    # Given: a state with one skill.
    path = tmp_path / "state.json"
    _ = save_state(path, ManagedSyncState(skills={"managed-example": SkillState()}))

    # When: the on-disk document is parsed independently.
    payload = _JSON_LOADS(path.read_text(encoding="utf-8"))

    # Then: it has the exact v1 top-level and per-skill shape.
    assert payload == {
        "schema_version": 1,
        "skills": {
            "managed-example": {
                "highest_sequence": 0,
                "last_verified_digest": None,
                "activated_digest": None,
                "revoked_digests": [],
            },
        },
    }
