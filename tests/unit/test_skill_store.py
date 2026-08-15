from __future__ import annotations

import io
import tarfile
from pathlib import Path

from automation.node_asset_renderer import render_asset
from automation.node_config import default_node_config

import pytest

from automation import skill_store
from automation.managed_skills import manifest
from automation.skill_review import skill_digest


ROOT = Path(__file__).resolve().parents[2]
SUDOERS_SEED = ROOT / "automation" / "sudoers.d" / "autophagy-skill-store"


def _skill(tmp_path: Path, greeting: str = "hello") -> Path:
    skill = tmp_path / "hello-autophagy"
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    _ = (skill / "SKILL.md").write_text(
        "---\nname: hello-autophagy\ndescription: Safe deterministic greeting.\n---\n",
        encoding="utf-8",
    )
    scenario = scripts / "scenario.sh"
    _ = scenario.write_text(f"#!/bin/sh\nprintf '{greeting}\\n'\n", encoding="utf-8")
    scenario.chmod(0o755)
    return skill


def _named_skill(source_root: Path, name: str) -> Path:
    renamed = source_root / name
    _skill(source_root).rename(renamed)
    return renamed


def _archive(skill: Path, destination: Path) -> Path:
    with tarfile.open(destination, "w:gz") as archive:
        archive.add(skill, arcname=skill.name)
    return destination


def _install_managed(source_root: Path, store: Path, name: str) -> Path:
    skill = _named_skill(source_root, name)
    archive = _archive(skill, source_root / "managed.tar.gz")
    return skill_store.install_managed_archive(_request(skill, archive, store), "cha")


def _request(skill: Path, archive: Path, store: Path, digest: str | None = None) -> skill_store.InstallRequest:
    return skill_store.InstallRequest(archive, store, skill.name, digest or skill_digest(skill))


def test_install_archive_when_digest_matches_then_publishes_read_only_release(tmp_path: Path) -> None:
    # Given
    skill = _skill(tmp_path)
    request = _request(skill, _archive(skill, tmp_path / "skill.tar.gz"), tmp_path / "store")

    # When
    release = skill_store.install_archive(request)

    # Then
    live = request.store_root / "live" / skill.name
    assert release == request.store_root / "releases" / skill.name / skill_digest(skill)
    assert live.is_symlink()
    assert live.resolve() == release
    assert skill_digest(live) == skill_digest(skill)
    assert all(path.stat().st_mode & 0o022 == 0 for path in (release, *release.rglob("*")))
    assert (release / "scripts" / "scenario.sh").stat().st_mode & 0o111


def test_parse_command_when_install_managed_shape_is_given_then_returns_managed_command() -> None:
    # Given: the managed-install command shape introduced by MS-N1.
    command = ("install-managed", "--publisher", "cha", "--skill", "managed-x", "--hash", "a" * 64)

    # When: the parser reads the publisher-fed verb.
    parsed = skill_store._parse_command(command)

    # Then: the managed verb parses into the publisher-scoped install command.
    assert parsed == skill_store.InstallManagedCommand("cha", "managed-x", "a" * 64)


def _digest_fixture(tmp_path: Path) -> Path:
    skill = tmp_path / "digest-fixture"
    (skill / "nested").mkdir(parents=True)
    _ = (skill / "alpha.txt").write_bytes(b"alpha\n")
    _ = (skill / "nested" / "b.txt").write_bytes(b"bravo\n")
    return skill


def test_skill_digest_when_fixture_tree_is_hashed_then_returns_stable_golden(tmp_path: Path) -> None:
    # Given: a two-file tree with nested relative paths.
    skill = _digest_fixture(tmp_path)

    # When: the store digest is computed.
    digest = skill_store._skill_digest(skill)

    # Then: the current per-file hash format produces the stable identity value.
    assert digest == "ab6a3d02fdaf378b61a14283e2a630ed3ae89fcbbe44c8b436a01f24995b1f9c"


def test_skill_digest_when_review_and_store_hash_same_tree_then_values_are_equal(tmp_path: Path) -> None:
    # Given: one fixture tree shared by both digest implementations.
    skill = _digest_fixture(tmp_path)

    # When: each module computes the tree identity.
    store_digest = skill_store._skill_digest(skill)
    review_digest = skill_digest(skill)

    # Then: the manifest-facing review identity equals the store identity.
    assert store_digest == review_digest


def test_install_archive_when_name_is_maximum_length_then_publishes(tmp_path: Path) -> None:
    # Given: a valid archive whose root directory has the maximum accepted 41-character name.
    skill_name = "a" + "b" * 40
    skill = _named_skill(tmp_path, skill_name)
    archive = _archive(skill, tmp_path / "skill.tar.gz")
    request = _request(skill, archive, tmp_path / "store")

    # When: the store validates and installs the archive.
    release = skill_store.install_archive(request)

    # Then: the maximum-length name is published and live-linked.
    assert release == request.store_root / "releases" / skill_name / request.expected_digest
    assert (request.store_root / "live" / skill_name).resolve() == release


def test_install_archive_when_skill_name_is_42_characters_then_rejects_before_publish(
    tmp_path: Path,
) -> None:
    # Given: a minimal valid archive using the one-character-over-limit name.
    skill = _skill(tmp_path)
    renamed = tmp_path / ("a" + "b" * 41)
    skill.rename(renamed)
    archive = _archive(renamed, tmp_path / "skill.tar.gz")
    request = _request(renamed, archive, tmp_path / "store")

    # When / Then: current name validation rejects the archive before any live entry exists.
    with pytest.raises(skill_store.SkillStoreError, match="invalid skill name"):
        _ = skill_store.install_archive(request)
    assert not (request.store_root / "live").exists()


def test_install_archive_when_digest_differs_then_leaves_live_index_unchanged(tmp_path: Path) -> None:
    # Given
    skill = _skill(tmp_path)
    request = _request(skill, _archive(skill, tmp_path / "skill.tar.gz"), tmp_path / "store", "b" * 64)

    # When / Then
    with pytest.raises(skill_store.SkillStoreError, match="digest mismatch"):
        _ = skill_store.install_archive(request)
    assert not (request.store_root / "live" / skill.name).exists()


@pytest.mark.parametrize("member_type", (tarfile.SYMTYPE, tarfile.LNKTYPE), ids=("symlink", "hardlink"))
def test_install_archive_when_archive_contains_link_then_rejects(tmp_path: Path, member_type: bytes) -> None:
    # Given
    archive_path = tmp_path / "skill.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        directory = tarfile.TarInfo("hello-autophagy")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        link = tarfile.TarInfo("hello-autophagy/SKILL.md")
        link.type = member_type
        link.linkname = "/etc/passwd"
        archive.addfile(link)
    request = skill_store.InstallRequest(archive_path, tmp_path / "store", "hello-autophagy", "a" * 64)

    # When / Then
    with pytest.raises(skill_store.SkillStoreError, match="regular files and directories"):
        _ = skill_store.install_archive(request)


def test_install_archive_when_archive_escapes_skill_root_then_rejects(tmp_path: Path) -> None:
    # Given
    archive_path = tmp_path / "skill.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        payload = b"escape"
        member = tarfile.TarInfo("hello-autophagy/../../escape")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    request = skill_store.InstallRequest(archive_path, tmp_path / "store", "hello-autophagy", "a" * 64)

    # When / Then
    with pytest.raises(skill_store.SkillStoreError, match="unsafe archive path"):
        _ = skill_store.install_archive(request)
    assert not (tmp_path / "escape").exists()


def test_install_archive_when_replacing_release_then_switches_live_atomically(tmp_path: Path) -> None:
    # Given
    store = tmp_path / "store"
    first = _skill(tmp_path / "first", "first")
    first_release = skill_store.install_archive(_request(first, _archive(first, tmp_path / "first.tar.gz"), store))
    second = _skill(tmp_path / "second", "second")

    # When
    second_release = skill_store.install_archive(_request(second, _archive(second, tmp_path / "second.tar.gz"), store))

    # Then
    assert first_release.is_dir()
    assert (store / "live" / second.name).resolve() == second_release
    deployed = (second_release / "scripts" / "scenario.sh").read_bytes()
    assert deployed == (second / "scripts" / "scenario.sh").read_bytes()


def test_remove_skill_when_live_link_exists_then_unpublishes_only_live_entry(tmp_path: Path) -> None:
    # Given
    skill = _skill(tmp_path)
    store = tmp_path / "store"
    release = skill_store.install_archive(_request(skill, _archive(skill, tmp_path / "skill.tar.gz"), store))

    # When
    removed = skill_store.remove_skill(store, skill.name)

    # Then
    assert removed is True
    assert not (store / "live" / skill.name).exists()
    assert release.is_dir()


def test_install_archive_when_name_is_managed_prefixed_then_rejects_forgery(tmp_path: Path) -> None:
    # Given: a valid archive whose root claims a managed-namespace name via the plain verb.
    skill = _named_skill(tmp_path, "managed-x")
    request = _request(skill, _archive(skill, tmp_path / "skill.tar.gz"), tmp_path / "store")

    # When / Then: the plain install path refuses to forge into the managed namespace.
    with pytest.raises(skill_store.SkillStoreError, match="managed namespace is publisher-fed"):
        _ = skill_store.install_archive(request)
    assert not (request.store_root / "live").exists()
    assert not (request.store_root / "releases").exists()


def test_install_managed_archive_when_digest_matches_then_publishes_publisher_release(tmp_path: Path) -> None:
    # Given: a valid managed-prefixed archive published by cha.
    skill = _named_skill(tmp_path, "managed-x")
    store = tmp_path / "store"
    request = _request(skill, _archive(skill, tmp_path / "skill.tar.gz"), store)

    # When: the managed verb installs the archive.
    release = skill_store.install_managed_archive(request, "cha")

    # Then: the release is immutable under the publisher namespace and live-linked in the shared dir.
    assert release == store / "managed-releases" / "cha" / "managed-x" / request.expected_digest
    live = store / "live" / "managed-x"
    assert live.is_symlink()
    assert live.resolve() == release
    assert all(path.stat().st_mode & 0o022 == 0 for path in (release, *release.rglob("*")))


def test_install_managed_archive_when_skill_lacks_prefix_then_rejects(tmp_path: Path) -> None:
    # Given: a valid archive whose root name is outside the managed namespace.
    skill = _named_skill(tmp_path, "plain-skill")
    request = _request(skill, _archive(skill, tmp_path / "skill.tar.gz"), tmp_path / "store")

    # When / Then: the managed verb only accepts managed-prefixed names.
    with pytest.raises(skill_store.SkillStoreError, match="managed skill name must start with managed-"):
        _ = skill_store.install_managed_archive(request, "cha")
    assert not (request.store_root / "live").exists()


def test_install_managed_archive_when_publisher_is_invalid_then_rejects(tmp_path: Path) -> None:
    # Given: a valid managed archive attributed to a malformed publisher name.
    skill = _named_skill(tmp_path, "managed-x")
    request = _request(skill, _archive(skill, tmp_path / "skill.tar.gz"), tmp_path / "store")

    # When / Then: the publisher name gate rejects before anything is published.
    with pytest.raises(skill_store.SkillStoreError, match="invalid publisher name"):
        _ = skill_store.install_managed_archive(request, "Cha!")
    assert not (request.store_root / "live").exists()


@pytest.mark.parametrize(
    ("publisher", "skill_name", "expected"),
    (
        ("cha", "plain-skill", "managed skill name must start with managed-"),
        ("Invalid", "managed-x", "invalid publisher name"),
        ("-bad", "managed-x", "invalid publisher name"),
        ("a" * 33, "managed-x", "invalid publisher name"),
        ("cha", "managed-" + "b" * 34, "invalid skill name"),
    ),
    ids=(
        "unprefixed-skill",
        "uppercase-publisher",
        "leading-dash-publisher",
        "33-char-publisher",
        "42-char-managed-name",
    ),
)
def test_parse_command_when_install_managed_input_is_invalid_then_rejects(
    publisher: str, skill_name: str, expected: str
) -> None:
    # Given: an install-managed argv with one invalid field.
    command = ("install-managed", "--publisher", publisher, "--skill", skill_name, "--hash", "a" * 64)

    # When / Then: parsing fails closed before any stdin archive is consumed.
    with pytest.raises(skill_store.SkillStoreError, match=expected):
        _ = skill_store._parse_command(command)


def test_install_archive_when_managed_live_twin_exists_then_rejects_base_name(tmp_path: Path) -> None:
    # Given: a live managed-xy entry published through the managed verb.
    store = tmp_path / "store"
    _ = _install_managed(tmp_path / "managed", store, "managed-xy")
    base = _named_skill(tmp_path / "base", "xy")
    request = _request(base, _archive(base, tmp_path / "base.tar.gz"), store)

    # When / Then: the plain verb refuses the colliding base name (SI-4).
    with pytest.raises(skill_store.SkillStoreError, match="collides with managed live entry"):
        _ = skill_store.install_archive(request)
    assert not (store / "live" / "xy").exists()


def test_install_managed_archive_when_base_live_entry_exists_then_rejects(tmp_path: Path) -> None:
    # Given: a live base xy entry published through the plain verb.
    store = tmp_path / "store"
    base = _named_skill(tmp_path / "base", "xy")
    _ = skill_store.install_archive(_request(base, _archive(base, tmp_path / "base.tar.gz"), store))
    managed = _named_skill(tmp_path / "managed", "managed-xy")
    request = _request(managed, _archive(managed, tmp_path / "managed.tar.gz"), store)

    # When / Then: the managed verb refuses the colliding managed twin (SI-4).
    with pytest.raises(skill_store.SkillStoreError, match="collides with base live entry"):
        _ = skill_store.install_managed_archive(request, "cha")
    assert (store / "live" / "xy").is_symlink()
    assert not (store / "live" / "managed-xy").exists()


def test_install_managed_archive_when_publishing_then_leaves_releases_dir_untouched(tmp_path: Path) -> None:
    # Given: a store that already carries one plain release.
    store = tmp_path / "store"
    base = _named_skill(tmp_path / "base", "existing-skill")
    _ = skill_store.install_archive(_request(base, _archive(base, tmp_path / "base.tar.gz"), store))
    releases_before = sorted(path.relative_to(store) for path in (store / "releases").rglob("*"))

    # When: a managed release is published into the same store.
    _ = _install_managed(tmp_path / "managed", store, "managed-x")

    # Then: the non-managed release store is byte-for-byte untouched.
    releases_after = sorted(path.relative_to(store) for path in (store / "releases").rglob("*"))
    assert releases_after == releases_before


def test_install_managed_archive_when_archive_escapes_skill_root_then_rejects(tmp_path: Path) -> None:
    # Given: a managed-verb archive carrying a path-traversal member.
    archive_path = tmp_path / "skill.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        payload = b"escape"
        member = tarfile.TarInfo("managed-x/../../escape")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    request = skill_store.InstallRequest(archive_path, tmp_path / "store", "managed-x", "a" * 64)

    # When / Then: the shared archive sanity path applies unchanged to the managed verb.
    with pytest.raises(skill_store.SkillStoreError, match="unsafe archive path"):
        _ = skill_store.install_managed_archive(request, "cha")
    assert not (tmp_path / "escape").exists()


def test_remove_skill_when_managed_live_link_exists_then_unpublishes_only_live_entry(tmp_path: Path) -> None:
    # Given: a live managed skill.
    store = tmp_path / "store"
    release = _install_managed(tmp_path, store, "managed-x")

    # When: the existing remove verb unpublishes it.
    removed = skill_store.remove_skill(store, "managed-x")

    # Then: only the live link is gone; the immutable managed release is retained.
    assert removed is True
    assert not (store / "live" / "managed-x").exists()
    assert release.is_dir()


def test_sudoers_seed_when_read_then_allows_exactly_three_command_shapes() -> None:
    # Given: the deployed sudoers seed for the root helper.
    helper = "/usr/local/libexec/autophagy-install-skill"
    lines = render_asset(SUDOERS_SEED, default_node_config()).splitlines()

    # When: the allowed command shapes are extracted.
    shapes = [line.split(f"{helper} ", 1)[1] for line in lines]

    # Then: exactly the three root-only NOPASSWD shapes are granted.
    assert all(line.startswith(f"operator ALL=(root) NOPASSWD: {helper} ") for line in lines)
    assert shapes == [
        "install --skill * --hash *",
        "install-managed --publisher * --skill * --hash *",
        "remove --skill *",
    ]


def test_managed_constants_when_compared_with_manifest_module_then_match_by_value() -> None:
    # Given: the import-free root helper and the manifest single source of truth.
    # When / Then: the by-value mirrored constants have not drifted (helper stays single-file).
    assert skill_store.MANAGED_PREFIX == manifest.MANAGED_PREFIX
    assert skill_store.MAX_SKILL_NAME == manifest.MAX_SKILL_NAME
