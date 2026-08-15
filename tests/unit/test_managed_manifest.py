from __future__ import annotations

import dataclasses
import hashlib
import json

import pytest

from automation.managed_skills.manifest import (
    MANAGED_PREFIX,
    MAX_SKILL_NAME,
    ManagedManifest,
    ManifestError,
    canonical_json,
    manifest_digest,
    parse_manifest,
)

_HEX64 = "a" * 64
_HEX64_B = "b" * 64
_COMMIT40 = "c" * 40


def _valid() -> dict[str, object]:
    return {
        "schema_version": 1,
        "publisher": "cha",
        "skill": "managed-hello",
        "release_sequence": 1,
        "source_commit": _COMMIT40,
        "skill_sha256": _HEX64,
        "previous_sha256": None,
        "compatibility": "any",
        "breaking": False,
        "revoked_digests": [],
        "changelog": "initial release",
    }


def _parse(payload: dict[str, object]) -> ManagedManifest:
    return parse_manifest(json.dumps(payload))


def _rejects(payload: dict[str, object], named_field: str) -> None:
    with pytest.raises(ManifestError) as excinfo:
        _parse(payload)
    assert named_field in str(excinfo.value)


def test_valid_manifest_parses_to_frozen_dataclass() -> None:
    manifest = _parse(_valid())
    assert manifest.schema_version == 1
    assert manifest.publisher == "cha"
    assert manifest.skill == "managed-hello"
    assert manifest.release_sequence == 1
    assert manifest.source_commit == _COMMIT40
    assert manifest.skill_sha256 == _HEX64
    assert manifest.previous_sha256 is None
    assert manifest.compatibility == "any"
    assert manifest.breaking is False
    assert manifest.revoked_digests == ()
    assert isinstance(manifest.revoked_digests, tuple)
    assert manifest.changelog == "initial release"
    assert manifest.migration is None


def test_manifest_is_frozen() -> None:
    manifest = _parse(_valid())
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(manifest, "publisher", "mallory")  # noqa: B010


@pytest.mark.parametrize("field", sorted(_valid()))
def test_missing_required_field_error_names_the_field(field: str) -> None:
    payload = _valid()
    del payload[field]
    _rejects(payload, field)


@pytest.mark.parametrize(
    "bad_name",
    [
        "calendar",
        "hello-managed",
        "managed-",
        "managed--x",
        "managed-Hello",
        "managed-hello_world",
        "MANAGED-hello",
        "managed- hello",
    ],
)
def test_skill_name_must_match_managed_pattern(bad_name: str) -> None:
    _rejects({**_valid(), "skill": bad_name}, "skill")


def test_skill_name_length_boundary_41_accepted_42_rejected() -> None:
    base_33 = "a" * 33
    accepted = MANAGED_PREFIX + base_33
    assert len(accepted) == MAX_SKILL_NAME == 41
    assert _parse({**_valid(), "skill": accepted}).skill == accepted
    _rejects({**_valid(), "skill": MANAGED_PREFIX + "a" * 34}, "skill")


@pytest.mark.parametrize(
    "bad_digest",
    ["a" * 63, "a" * 65, "A" * 64, "g" * 64, "", 42],
)
def test_skill_sha256_must_be_64_hex_lowercase(bad_digest: object) -> None:
    _rejects({**_valid(), "skill_sha256": bad_digest}, "skill_sha256")


def test_previous_sha256_is_nullable_but_validated_when_present() -> None:
    assert _parse({**_valid(), "previous_sha256": None}).previous_sha256 is None
    assert _parse({**_valid(), "previous_sha256": _HEX64_B}).previous_sha256 == _HEX64_B
    _rejects({**_valid(), "previous_sha256": "b" * 63}, "previous_sha256")
    _rejects({**_valid(), "previous_sha256": "B" * 64}, "previous_sha256")


def test_revoked_digests_may_be_empty_and_each_entry_is_validated() -> None:
    assert _parse({**_valid(), "revoked_digests": []}).revoked_digests == ()
    parsed = _parse({**_valid(), "revoked_digests": [_HEX64, _HEX64_B]})
    assert parsed.revoked_digests == (_HEX64, _HEX64_B)
    _rejects({**_valid(), "revoked_digests": ["z" * 64]}, "revoked_digests")
    _rejects({**_valid(), "revoked_digests": [_HEX64, "b" * 63]}, "revoked_digests")
    _rejects({**_valid(), "revoked_digests": _HEX64}, "revoked_digests")


@pytest.mark.parametrize("bad_sequence", [0, -1, "1", 1.5, True, None])
def test_release_sequence_must_be_positive_int(bad_sequence: object) -> None:
    _rejects({**_valid(), "release_sequence": bad_sequence}, "release_sequence")


def test_release_sequence_one_is_the_minimum_accepted() -> None:
    assert _parse({**_valid(), "release_sequence": 1}).release_sequence == 1


def test_breaking_true_requires_non_empty_migration() -> None:
    _rejects({**_valid(), "breaking": True}, "migration")
    _rejects({**_valid(), "breaking": True, "migration": ""}, "migration")
    _rejects({**_valid(), "breaking": True, "migration": None}, "migration")
    parsed = _parse({**_valid(), "breaking": True, "migration": "run scripts/migrate.sh"})
    assert parsed.breaking is True
    assert parsed.migration == "run scripts/migrate.sh"


def test_breaking_false_does_not_require_migration() -> None:
    assert _parse(_valid()).migration is None
    assert _parse({**_valid(), "migration": None}).migration is None


def test_breaking_must_be_a_real_bool() -> None:
    _rejects({**_valid(), "breaking": 1}, "breaking")
    _rejects({**_valid(), "breaking": "true"}, "breaking")


def test_compatibility_must_be_non_empty_string() -> None:
    _rejects({**_valid(), "compatibility": ""}, "compatibility")
    _rejects({**_valid(), "compatibility": None}, "compatibility")
    assert _parse({**_valid(), "compatibility": "any"}).compatibility == "any"


def test_publisher_must_be_non_empty_string() -> None:
    _rejects({**_valid(), "publisher": ""}, "publisher")
    _rejects({**_valid(), "publisher": 7}, "publisher")


def test_changelog_must_be_string() -> None:
    _rejects({**_valid(), "changelog": None}, "changelog")
    _rejects({**_valid(), "changelog": 3}, "changelog")


def test_source_commit_is_forty_hex_or_none() -> None:
    assert _parse({**_valid(), "source_commit": None}).source_commit is None
    assert _parse({**_valid(), "source_commit": _COMMIT40}).source_commit == _COMMIT40
    _rejects({**_valid(), "source_commit": "c" * 39}, "source_commit")
    _rejects({**_valid(), "source_commit": "C" * 40}, "source_commit")
    _rejects({**_valid(), "source_commit": "not-a-commit"}, "source_commit")


def test_schema_version_must_be_one() -> None:
    _rejects({**_valid(), "schema_version": 2}, "schema_version")
    _rejects({**_valid(), "schema_version": "1"}, "schema_version")
    _rejects({**_valid(), "schema_version": True}, "schema_version")


def test_unknown_keys_are_rejected_fail_closed() -> None:
    _rejects({**_valid(), "extra_field": 1}, "extra_field")


def test_invalid_json_is_a_manifest_error() -> None:
    with pytest.raises(ManifestError):
        parse_manifest("not json {")
    with pytest.raises(ManifestError):
        parse_manifest('["not", "an", "object"]')


def test_canonical_json_is_byte_stable() -> None:
    manifest = _parse(_valid())
    expected = json.dumps(
        {**_valid(), "migration": None}, sort_keys=True, separators=(",", ":")
    )
    assert canonical_json(manifest) == expected
    assert canonical_json(manifest) == canonical_json(manifest)


def test_manifest_digest_is_sha256_of_canonical_form_and_reproducible() -> None:
    manifest = _parse(_valid())
    expected = hashlib.sha256(
        json.dumps(
            {**_valid(), "migration": None}, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert manifest_digest(manifest) == expected
    assert manifest_digest(manifest) == manifest_digest(manifest)


def test_parse_canonical_parse_roundtrip_is_equal() -> None:
    manifest = _parse(
        {
            **_valid(),
            "breaking": True,
            "migration": "run scripts/migrate.sh",
            "revoked_digests": [_HEX64_B],
            "previous_sha256": _HEX64_B,
        }
    )
    assert parse_manifest(canonical_json(manifest)) == manifest
