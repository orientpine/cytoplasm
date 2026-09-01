from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from skills.proposal.scripts import proposal_version  # noqa: E402


def _create(store: proposal_version.VersionStore, slug: str, delta: str) -> dict[str, object]:
    parent = store.head(slug)
    parent_sha = store.manifest_sha256(slug, parent) if parent else ""
    run_key = store.compute_run_key(parent_sha, [delta], {}, "template", "30-page", {})
    staging = store.begin(slug, run_key)
    if isinstance(staging, proposal_version.Reused):
        return {"version": staging.version, "run_key": run_key, "reused": True}
    version = store.promote(slug, staging, {"parent": parent, "schema_version": 1})
    return {"version": version, "run_key": run_key, "reused": False}


def test_first_create_sets_version_and_head(tmp_path: Path) -> None:
    store = proposal_version.VersionStore(tmp_path)

    result = _create(store, "demo", "delta-a")

    assert result["version"] == "v000001"
    assert store.head("demo") == "v000001"
    assert (tmp_path / "demo" / "versions" / "v000001").is_dir()


def test_same_run_key_reuses_existing_version(tmp_path: Path) -> None:
    store = proposal_version.VersionStore(tmp_path)
    first = _create(store, "demo", "delta-a")

    reused = store.begin("demo", str(first["run_key"]))

    assert isinstance(reused, proposal_version.Reused)
    assert reused.version == "v000001"
    assert [path.name for path in (tmp_path / "demo" / "versions").iterdir()] == ["v000001"]


def test_different_delta_creates_child_version(tmp_path: Path) -> None:
    store = proposal_version.VersionStore(tmp_path)
    _create(store, "demo", "delta-a")

    result = _create(store, "demo", "delta-b")

    manifest = json.loads(
        (tmp_path / "demo" / "versions" / "v000002" / "manifest.json").read_text()
    )
    assert result["version"] == "v000002"
    assert manifest["parent"] == "v000001"


def test_promotion_serializes_before_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = proposal_version.VersionStore(tmp_path)
    staging_a = store.begin("demo", "a" * 64)
    staging_b = store.begin("demo", "b" * 64)
    assert isinstance(staging_a, proposal_version.Staging)
    assert isinstance(staging_b, proposal_version.Staging)
    real_replace = os.replace

    def contend_after_staging_replace(
        source: str | os.PathLike[str], target: str | os.PathLike[str]
    ) -> None:
        real_replace(source, target)
        if Path(source) == staging_a.path:
            with pytest.raises(proposal_version.VersionLocked):
                store.promote("demo", staging_b, {"parent": None, "schema_version": 1})

    monkeypatch.setattr(os, "replace", contend_after_staging_replace)

    version = store.promote("demo", staging_a, {"parent": None, "schema_version": 1})

    assert version == "v000001"
    assert store.head("demo") == version
    assert staging_b.path.is_dir()
    assert [path.name for path in (tmp_path / "demo" / "versions").iterdir()] == [version]


def test_stale_parent_conflict_preserves_staging_and_versions(tmp_path: Path) -> None:
    store = proposal_version.VersionStore(tmp_path)
    _create(store, "demo", "delta-a")
    staging = store.begin("demo", "b" * 64)
    assert isinstance(staging, proposal_version.Staging)
    versions = tmp_path / "demo" / "versions"
    before = sorted(path.name for path in versions.iterdir())

    with pytest.raises(proposal_version.HeadCasConflict):
        store.promote("demo", staging, {"parent": None, "schema_version": 1})

    assert staging.path.is_dir()
    assert sorted(path.name for path in versions.iterdir()) == before


def test_two_promotions_from_same_parent_publish_only_first(tmp_path: Path) -> None:
    store = proposal_version.VersionStore(tmp_path)
    staging_a = store.begin("demo", "a" * 64)
    staging_b = store.begin("demo", "b" * 64)
    assert isinstance(staging_a, proposal_version.Staging)
    assert isinstance(staging_b, proposal_version.Staging)

    version = store.promote("demo", staging_a, {"parent": None, "schema_version": 1})
    with pytest.raises(proposal_version.HeadCasConflict):
        store.promote("demo", staging_b, {"parent": None, "schema_version": 1})

    versions = list((tmp_path / "demo" / "versions").iterdir())
    assert version == "v000001"
    assert store.head("demo") == version
    assert len(versions) == 1
    assert staging_b.path.is_dir()


def test_failed_promotion_keeps_staging_and_versions_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = proposal_version.VersionStore(tmp_path)
    staging = store.begin("demo", "a" * 64)
    assert isinstance(staging, proposal_version.Staging)
    real_replace = os.replace

    def fail_staging_replace(
        source: str | os.PathLike[str], target: str | os.PathLike[str]
    ) -> None:
        if Path(source) == staging.path:
            raise OSError("injected rename failure")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_staging_replace)

    with pytest.raises(OSError, match="injected"):
        store.promote("demo", staging, {"parent": None, "schema_version": 1})

    assert staging.path.is_dir()
    assert list((tmp_path / "demo" / "versions").iterdir()) == []


def test_private_modes_are_enforced(tmp_path: Path) -> None:
    store = proposal_version.VersionStore(tmp_path)
    _create(store, "demo", "delta-a")
    slug_dir = tmp_path / "demo"

    directories = [slug_dir, slug_dir / "versions", slug_dir / "versions" / "v000001"]
    directories.extend(
        (slug_dir / "versions" / "v000001" / name)
        for name in ("inputs", "corpus", "images", "out")
    )
    files = [slug_dir / "HEAD", slug_dir / "versions" / "v000001" / "manifest.json"]

    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in directories)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)


@pytest.mark.parametrize("slug", ["..", "../x", "Bad", "x" * 64])
def test_invalid_slugs_are_rejected(tmp_path: Path, slug: str) -> None:
    with pytest.raises(proposal_version.InvalidSlug):
        proposal_version.VersionStore(tmp_path).resolve_slug_dir(slug)


def test_noncanonical_root_is_normalized(tmp_path: Path) -> None:
    canonical_root = tmp_path / "store"
    root_with_parent_segment = tmp_path / "discarded" / ".." / "store"

    store = proposal_version.VersionStore(root_with_parent_segment)
    _create(store, "demo", "delta-a")

    assert store.root == canonical_root.resolve()
    assert (canonical_root / "demo" / "versions" / "v000001").is_dir()


def test_symlink_slug_component_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(proposal_version.InvalidSlug):
        proposal_version.VersionStore(tmp_path).resolve_slug_dir("linked")


def test_run_key_has_no_wall_clock_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = proposal_version.VersionStore(tmp_path)
    first = store.compute_run_key(
        "parent", ["b", "a"], {"mode": "full"}, "tpl", "30-page", {"engine": "pin"}
    )
    monkeypatch.setattr(time, "time", lambda: 9999999999.0)

    second = store.compute_run_key(
        "parent", ["a", "b"], {"mode": "full"}, "tpl", "30-page", {"engine": "pin"}
    )

    assert first == second


def test_changelog_markdown_is_deterministic_from_json(tmp_path: Path) -> None:
    store = proposal_version.VersionStore(tmp_path)
    entry = {"version": "v000001", "changes": ["Added corpus", "Rendered output"]}

    store.append_changelog("demo", entry)
    first = (tmp_path / "demo" / "CHANGELOG.md").read_bytes()
    store.regenerate_changelog("demo")
    second = (tmp_path / "demo" / "CHANGELOG.md").read_bytes()

    assert first == second
    assert json.loads((tmp_path / "demo" / "changelog.json").read_text()) == [entry]
