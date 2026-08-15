from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation.managed_skills.manifest import canonical_json, parse_manifest
from automation.managed_skills.release_metadata import ReleaseMetadata
from automation.managed_skills.submission_artifact import (
    SubmissionPackageConfig,
    extract_submission,
    package_personal_skill,
    validate_submission_artifact,
)
from automation.managed_skills.submission_errors import SubmissionArtifactError
from automation.skill_review import skill_digest


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout.strip()


def _personal_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "personal-x"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    _ = (repo / "SKILL.md").write_text(
        "---\nname: personal-x\ndescription: Personal promotion fixture\n---\n\n# Fixture\n",
        encoding="utf-8",
    )
    scenario = scripts / "scenario.sh"
    _ = scenario.write_text(
        "#!/usr/bin/env bash\nprintf 'SCENARIO-PASS\\n'\n",
        encoding="utf-8",
    )
    _ = scenario.chmod(0o755)
    _ = _git(repo, "init", "-b", "main")
    _ = _git(repo, "config", "user.email", "member@example.invalid")
    _ = _git(repo, "config", "user.name", "member")
    _ = _git(repo, "config", "commit.gpgsign", "false")
    _ = _git(repo, "add", "SKILL.md", "scripts/scenario.sh")
    _ = _git(repo, "commit", "-m", "Add personal fixture")
    return repo


def _package_config(tmp_path: Path, repo: Path) -> SubmissionPackageConfig:
    return SubmissionPackageConfig(
        personal_repo=repo,
        managed_skill="managed-x",
        publisher="testlab",
        release_sequence=1,
        previous_sha256=None,
        metadata=ReleaseMetadata(
            compatibility="any",
            breaking=False,
            revoked_digests=(),
            changelog="Promote the personal fixture.",
            migration=None,
        ),
        output_dir=tmp_path / "submission",
    )


def test_package_when_personal_head_is_clean_then_artifacts_pin_the_committed_source(
    tmp_path: Path,
) -> None:
    # Given: a clean, committed personal skill repository.
    repo = _personal_repo(tmp_path)
    expected_head = _git(repo, "rev-parse", "HEAD")

    # When: the member packages a managed-skill candidate.
    artifact = package_personal_skill(_package_config(tmp_path, repo))

    # Then: the existing manifest schema carries the exact personal commit and candidate digest.
    manifest = parse_manifest(artifact.manifest_path.read_text(encoding="utf-8"))
    assert manifest == artifact.manifest
    assert artifact.manifest_path.read_text(encoding="utf-8") == canonical_json(manifest)
    assert manifest.source_commit == expected_head
    assert manifest.skill == "managed-x"
    with extract_submission(artifact) as source:
        assert skill_digest(source) == manifest.skill_sha256
        assert "name: managed-x" in (source / "SKILL.md").read_text(encoding="utf-8")
        assert (source / "scripts" / "scenario.sh").stat().st_mode & 0o111
    assert "name: personal-x" in (repo / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("dirty_kind", ("tracked", "untracked"))
def test_package_when_personal_worktree_is_not_clean_then_existing_provenance_gate_blocks(
    tmp_path: Path,
    dirty_kind: str,
) -> None:
    # Given: the W-F4-A personal repository has a tracked or untracked change.
    repo = _personal_repo(tmp_path)
    if dirty_kind == "tracked":
        _ = (repo / "SKILL.md").write_text("dirty\n", encoding="utf-8")
    else:
        _ = (repo / "untracked.py").write_text("DIRTY = True\n", encoding="utf-8")

    # When / Then: packaging fails before producing a submission artifact.
    with pytest.raises(SubmissionArtifactError, match="personal_provenance_check"):
        _ = package_personal_skill(_package_config(tmp_path, repo))
    assert not (tmp_path / "submission").exists()


def test_validate_when_tarball_is_tampered_then_rejects_before_materialization(
    tmp_path: Path,
) -> None:
    # Given: a valid package whose tarball bytes are changed after creation.
    artifact = package_personal_skill(_package_config(tmp_path, _personal_repo(tmp_path)))
    with artifact.tarball_path.open("ab") as handle:
        _ = handle.write(b"tampered")

    # When / Then: the pinned artifact cannot be accepted or extracted.
    with pytest.raises(SubmissionArtifactError, match="tarball sha256"):
        _ = validate_submission_artifact(
            artifact.tarball_path,
            artifact.manifest_path,
            expected_tarball_sha256=artifact.tarball_sha256,
        )
