from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path


def _git(repo: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ("git", "-C", str(repo), *arguments),
        check=True,
        capture_output=True,
    ).stdout


def _fixture_repo(tmp_path: Path) -> tuple[Path, bytes]:
    repo = tmp_path / "repo"
    (repo / "비공개" / "중첩").mkdir(parents=True)
    _ = (repo / "README.md").write_bytes(b"public\n")
    _ = (repo / "한글 문서.md").write_bytes(b"public Korean path\n")
    _ = (repo / "private.txt").write_bytes(b"private\n")
    _ = (repo / "비공개" / "숨김.md").write_bytes(b"private directory\n")
    _ = (repo / "비공개" / "중첩" / "숨김.txt").write_bytes(b"nested private\n")
    _ = (repo / "public-link").symlink_to("README.md")
    manifest = "# fixture exclusions\nprivate.txt\n비공개/\n".encode()
    _ = (repo / "manifest.txt").write_bytes(manifest)
    _ = _git(repo, "init", "-q", "-b", "main")
    _ = _git(repo, "config", "user.name", "fs3-test")
    _ = _git(repo, "config", "user.email", "fs3-test@example.invalid")
    _ = _git(repo, "add", ".")
    _ = _git(repo, "commit", "-q", "-m", "fixture")
    return repo, manifest


def _exclusions(manifest: bytes) -> tuple[bytes, ...]:
    return tuple(line for line in manifest.splitlines() if line and not line.startswith(b"#"))


def _is_public(path: bytes, exclusions: tuple[bytes, ...]) -> bool:
    return all(
        path != exclusion
        if not exclusion.endswith(b"/")
        else not path.startswith(exclusion)
        for exclusion in exclusions
    )


def _public_paths(repo: Path, ref: str, manifest: bytes) -> set[bytes]:
    paths = _git(repo, "ls-tree", "-rz", "--name-only", ref).split(b"\0")
    exclusions = _exclusions(manifest)
    return {path for path in paths if path and _is_public(path, exclusions)}


def _archive_public_paths(repo: Path, ref: str, manifest: bytes) -> set[bytes]:
    exclusions = _exclusions(manifest)
    with tarfile.open(fileobj=io.BytesIO(_git(repo, "archive", ref)), mode="r:") as archive:
        return {
            member.name.encode("utf-8")
            for member in archive.getmembers()
            if (member.isfile() or member.issym())
            and _is_public(member.name.encode("utf-8"), exclusions)
        }


def test_nul_ls_tree_public_set_matches_archive_leaf_members(tmp_path: Path) -> None:
    # Given
    repo, manifest = _fixture_repo(tmp_path)

    # When
    computed = _public_paths(repo, "HEAD", manifest)
    archived = _archive_public_paths(repo, "HEAD", manifest)

    # Then
    assert computed == archived
    assert "한글 문서.md".encode() in computed
    assert b"public-link" in computed


def test_newline_ls_tree_c_quotes_korean_path_and_produces_wrong_set(tmp_path: Path) -> None:
    # Given
    repo, manifest = _fixture_repo(tmp_path)
    expected = _archive_public_paths(repo, "HEAD", manifest)

    # When
    exclusions = _exclusions(manifest)
    quoted = {
        path
        for path in _git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
        if _is_public(path, exclusions)
    }

    # Then
    assert quoted != expected
    assert "한글 문서.md".encode() not in quoted
