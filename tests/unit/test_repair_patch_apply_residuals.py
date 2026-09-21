"""Real-git approval-byte and scope regressions, separate from frozen ops tests."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from automation.repair import repair_ops_git as git_ops

PATCH = (
    b"diff --git a/automation/example.txt b/automation/example.txt\n"
    b"--- a/automation/example.txt\n+++ b/automation/example.txt\n"
    b"@@ -1 +1 @@\n-old\n+approved\n"
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=root, capture_output=True, text=True, check=True, timeout=10,
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Repair Test")
    git(root, "config", "user.email", "repair@example.invalid")
    (root / "automation").mkdir()
    (root / "automation/example.txt").write_text("old\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "test: seed")
    return root


def test_apply_refuses_changed_bytes_when_digest_was_approved(repository: Path, tmp_path: Path) -> None:
    # Given: the patch on disk no longer matches the approved artifact.
    patch = tmp_path / "patch.diff"
    patch.write_bytes(PATCH.replace(b"approved", b"changed"))
    adapter = git_ops.GitRepository(repository, expected_patch_sha256=hashlib.sha256(PATCH).hexdigest())
    original_head = git(repository, "rev-parse", "HEAD")
    # When: the final mutation boundary rereads the artifact.
    with pytest.raises(git_ops.PatchDigestMismatch):
        adapter.apply(patch)
    # Then: both working tree and history remain unchanged.
    assert git(repository, "status", "--porcelain") == ""
    assert git(repository, "rev-parse", "HEAD") == original_head


@pytest.mark.parametrize("expected_digest", [None, hashlib.sha256(PATCH).hexdigest()])
def test_apply_uses_captured_bytes_when_file_changes_after_read(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expected_digest: str | None,
) -> None:
    # Given: a deterministic replacement immediately after the final byte read.
    patch = tmp_path / "patch.diff"
    patch.write_bytes(PATCH)
    original_read = Path.read_bytes

    def replace_after_read(path: Path) -> bytes:
        content = original_read(path)
        if path == patch:
            patch.write_bytes(PATCH.replace(b"approved", b"changed"))
        return content

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    adapter = git_ops.GitRepository(repository, expected_patch_sha256=expected_digest)
    # When: git applies after the path has changed.
    adapter.apply(patch)
    # Then: stdin carries the approved bytes, not the replacement file.
    assert git(repository, "show", "HEAD:automation/example.txt") == "approved"
    assert git(repository, "status", "--porcelain") == ""


@pytest.mark.parametrize("rename", [False, True], ids=["delete", "rename"])
def test_apply_stages_both_sides_when_patch_removes_a_path(
    repository: Path, tmp_path: Path, rename: bool,
) -> None:
    # Given: a real git deletion or rename diff approved by its bytes.
    source = repository / "automation/example.txt"
    if rename:
        git(repository, "mv", "automation/example.txt", "automation/renamed.txt")
    else:
        git(repository, "rm", "automation/example.txt")
    content = git(repository, "diff", "--cached") + "\n"
    git(repository, "reset", "--hard", "HEAD")
    patch = tmp_path / "patch.diff"
    patch.write_text(content, encoding="utf-8")
    adapter = git_ops.GitRepository(repository, expected_patch_sha256=hashlib.sha256(content.encode()).hexdigest())
    # When: the approved file operation is applied and committed.
    adapter.apply(patch)
    # Then: the removed side is in the commit rather than left unstaged.
    assert not source.exists()
    assert git(repository, "ls-tree", "--name-only", "HEAD", "automation/example.txt") == ""
    assert git(repository, "status", "--porcelain") == ""
    if rename:
        assert git(repository, "show", "HEAD:automation/renamed.txt") == "old"


def test_scope_refuses_rename_when_old_path_is_outside_allowlist(tmp_path: Path) -> None:
    # Given: the destination is allowed but the rename would delete a private path.
    patch = tmp_path / "patch.diff"
    content = (
        b"diff --git a/private/data b/automation/data\n"
        b"similarity index 100%\nrename from private/data\nrename to automation/data\n"
        b"--- a/private/data\n+++ b/automation/data\n"
    )
    patch.write_bytes(content)
    adapter = git_ops.GitRepository(tmp_path, expected_patch_sha256=hashlib.sha256(content).hexdigest())
    # When / Then: scope checking rejects the source before git runs.
    with pytest.raises(git_ops.RepairOpsError, match="repository code or config only"):
        adapter.apply(patch)
