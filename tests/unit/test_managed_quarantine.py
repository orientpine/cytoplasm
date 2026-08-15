from __future__ import annotations

import json
import os
import stat
from datetime import datetime
from pathlib import Path

import pytest

from automation import skill_store
from automation.managed_skills.manifest import ManagedManifest, canonical_json
from automation.managed_sync import quarantine
from automation.managed_sync.verify import VerifiedRelease

_DIGEST = "a" * 64


def _manifest(skill: str, sequence: int, digest: str) -> ManagedManifest:
    return ManagedManifest(
        schema_version=1,
        publisher="cha",
        skill=skill,
        release_sequence=sequence,
        source_commit=None,
        skill_sha256=digest,
        previous_sha256=None,
        compatibility="any",
        breaking=False,
        revoked_digests=(),
        changelog="test release",
        migration=None,
    )


def _verified(tree: Path, sequence: int = 2, digest: str = _DIGEST) -> VerifiedRelease:
    skill = "managed-demo"
    return VerifiedRelease(
        skill=skill,
        sequence=sequence,
        digest=digest,
        manifest=_manifest(skill, sequence, digest),
        tree_path=tree,
        tag=f"{skill}/v{sequence}",
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    tree = tmp_path / "verified" / "managed-demo"
    (tree / "scripts").mkdir(parents=True)
    _ = (tree / "SKILL.md").write_text("---\nname: managed-demo\n---\n", encoding="utf-8")
    _ = (tree / "scripts" / "demo_cli.py").write_text("print('demo')\n", encoding="utf-8")
    return tree


def test_stage_candidate_when_tree_is_sane_then_stages_inner_tree_with_metadata(
    tmp_path: Path, tree: Path
) -> None:
    root = tmp_path / "quarantine"
    verified = _verified(tree)

    staged = quarantine.stage_candidate(verified, root)

    assert staged == root / "managed-demo" / _DIGEST
    inner = staged / "managed-demo"
    assert (inner / "SKILL.md").read_text(encoding="utf-8") == "---\nname: managed-demo\n---\n"
    assert (inner / "scripts" / "demo_cli.py").is_file()
    expected_manifest = canonical_json(verified.manifest) + "\n"
    assert (staged / "manifest.json").read_text(encoding="utf-8") == expected_manifest
    provenance = json.loads((staged / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["publisher"] == "cha"
    assert provenance["tag"] == "managed-demo/v2"
    assert provenance["sequence"] == 2
    assert datetime.fromisoformat(provenance["verified_at"]).tzinfo is not None


def test_stage_candidate_when_staged_then_directories_are_private(
    tmp_path: Path, tree: Path
) -> None:
    root = tmp_path / "quarantine"

    staged = quarantine.stage_candidate(_verified(tree), root)

    private = (staged, staged.parent, staged / "managed-demo", staged / "managed-demo" / "scripts")
    for directory in private:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_stage_candidate_when_digest_already_staged_then_returns_existing_untouched(
    tmp_path: Path, tree: Path
) -> None:
    root = tmp_path / "quarantine"
    existing = root / "managed-demo" / _DIGEST
    existing.mkdir(parents=True)
    sentinel = existing / "sentinel"
    _ = sentinel.write_text("keep\n", encoding="utf-8")

    staged = quarantine.stage_candidate(_verified(tree), root)

    assert staged == existing
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert not (existing / "manifest.json").exists()


def test_stage_candidate_when_tree_contains_symlink_then_fails_closed(
    tmp_path: Path, tree: Path
) -> None:
    (tree / "escape").symlink_to(tree / "SKILL.md")

    with pytest.raises(quarantine.QuarantineError, match="symlink"):
        _ = quarantine.stage_candidate(_verified(tree), tmp_path / "quarantine")

    assert not (tmp_path / "quarantine" / "managed-demo" / _DIGEST).exists()


def test_stage_candidate_when_tree_contains_non_regular_file_then_fails_closed(
    tmp_path: Path, tree: Path
) -> None:
    os.mkfifo(tree / "pipe")

    with pytest.raises(quarantine.QuarantineError, match="non-regular"):
        _ = quarantine.stage_candidate(_verified(tree), tmp_path / "quarantine")


def test_stage_candidate_when_tree_exceeds_size_cap_then_fails_closed(
    tmp_path: Path, tree: Path
) -> None:
    oversized = tree / "blob.bin"
    oversized.touch()
    os.truncate(oversized, quarantine._MAX_ARCHIVE_BYTES + 1)

    with pytest.raises(quarantine.QuarantineError, match="bytes"):
        _ = quarantine.stage_candidate(_verified(tree), tmp_path / "quarantine")


def test_stage_candidate_when_tree_exceeds_member_cap_then_fails_closed(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(quarantine, "_MAX_MEMBERS", 1)

    with pytest.raises(quarantine.QuarantineError, match="members"):
        _ = quarantine.stage_candidate(_verified(tree), tmp_path / "quarantine")


def test_sanity_limits_when_compared_to_skill_store_then_match_by_value() -> None:
    assert quarantine._MAX_ARCHIVE_BYTES == skill_store._MAX_ARCHIVE_BYTES
    assert quarantine._MAX_MEMBERS == skill_store._MAX_MEMBERS


def test_stage_candidate_when_rename_crashes_then_leaves_no_partial_digest_dir(
    tmp_path: Path, tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "quarantine"

    def broken_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("simulated crash")

    monkeypatch.setattr(quarantine.os, "replace", broken_replace)

    with pytest.raises(quarantine.QuarantineError, match="stage"):
        _ = quarantine.stage_candidate(_verified(tree), root)

    skill_dir = root / "managed-demo"
    assert not (skill_dir / _DIGEST).exists()
    assert list(skill_dir.iterdir()) == []
