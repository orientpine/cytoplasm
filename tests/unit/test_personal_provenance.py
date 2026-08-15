from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROVENANCE = ROOT / "automation" / "deploy_provenance.sh"
_HEAD = re.compile(r"[0-9a-f]{40,64}\Z")


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout.strip()


def _committed_personal_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "personal-skill"
    (repo / "scripts").mkdir(parents=True)
    _ = (repo / "SKILL.md").write_text(
        "---\nname: personal-skill\ndescription: Personal test skill\n---\n",
        encoding="utf-8",
    )
    scenario = repo / "scripts" / "scenario.sh"
    _ = scenario.write_text(
        "#!/usr/bin/env bash\necho SCENARIO-PASS\n",
        encoding="utf-8",
    )
    scenario.chmod(0o755)
    _ = _git(repo, "init", "-b", "main")
    _ = _git(repo, "config", "user.email", "personal@test.local")
    _ = _git(repo, "config", "user.name", "personal")
    _ = _git(repo, "config", "commit.gpgsign", "false")
    _ = _git(repo, "add", "SKILL.md", "scripts/scenario.sh")
    _ = _git(repo, "commit", "-m", "Add personal skill")
    return repo


def _personal_check(repo: Path, expected_head: str = "") -> subprocess.CompletedProcess[str]:
    arguments = f" {shlex.quote(expected_head)}" if expected_head else ""
    command = (
        f"source {shlex.quote(str(PROVENANCE))}; "
        f"personal_provenance_check {shlex.quote(str(repo))}{arguments}"
    )
    return subprocess.run(
        ("bash", "-c", command),
        capture_output=True,
        check=False,
        text=True,
    )


def test_personal_provenance_when_repo_is_clean_then_returns_committed_head(
    tmp_path: Path,
) -> None:
    # Given
    repo = _committed_personal_repo(tmp_path)
    expected = _git(repo, "rev-parse", "HEAD")

    # When
    result = _personal_check(repo)

    # Then
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    assert _HEAD.fullmatch(result.stdout.strip()) is not None
    assert "origin/main" not in result.stderr


def test_personal_provenance_when_tracked_file_is_dirty_then_blocks(tmp_path: Path) -> None:
    # Given
    repo = _committed_personal_repo(tmp_path)
    _ = (repo / "SKILL.md").write_text("dirty\n", encoding="utf-8")

    # When
    result = _personal_check(repo)

    # Then
    assert result.returncode == 1
    assert "DEPLOY-BLOCK" in result.stderr
    assert "worktree" in result.stderr


def test_personal_provenance_when_untracked_source_exists_then_names_and_blocks_it(
    tmp_path: Path,
) -> None:
    # Given
    repo = _committed_personal_repo(tmp_path)
    _ = (repo / "scripts" / "untracked.py").write_text(
        "NEW = True\n",
        encoding="utf-8",
    )

    # When
    result = _personal_check(repo)

    # Then
    assert result.returncode == 1
    assert "DEPLOY-BLOCK" in result.stderr
    assert "scripts/untracked.py" in result.stderr


def test_personal_provenance_when_only_ignored_residue_exists_then_allows_it(
    tmp_path: Path,
) -> None:
    # Given
    repo = _committed_personal_repo(tmp_path)
    _ = (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _ = _git(repo, "add", ".gitignore")
    _ = _git(repo, "commit", "-m", "Ignore runtime cache")
    cache = repo / "scripts" / "__pycache__"
    cache.mkdir()
    _ = (cache / "personal.cpython-312.pyc").write_bytes(b"compiled")

    # When
    result = _personal_check(repo)

    # Then
    assert result.returncode == 0, result.stderr


def test_personal_provenance_when_head_is_detached_then_blocks(tmp_path: Path) -> None:
    # Given
    repo = _committed_personal_repo(tmp_path)
    _ = _git(repo, "checkout", "--detach")

    # When
    result = _personal_check(repo)

    # Then
    assert result.returncode == 1
    assert "DEPLOY-BLOCK" in result.stderr
    assert "detached" in result.stderr


def test_personal_provenance_when_approval_head_differs_then_blocks(tmp_path: Path) -> None:
    # Given
    repo = _committed_personal_repo(tmp_path)
    other_head = "0" * 40

    # When
    result = _personal_check(repo, other_head)

    # Then
    assert result.returncode == 1
    assert "DEPLOY-BLOCK" in result.stderr
    assert "approval" in result.stderr
