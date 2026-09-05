"""Deploy guard: a tracked non-ASCII filename must not block an otherwise clean deploy.

Kept apart from ``test_deploy_provenance.py`` per ``tests/AGENTS.md`` — new cases go in
a new file so a settlement record pinning that file's output hash keeps replaying.

``git ls-files`` renders non-ASCII paths backslash-escaped and quoted unless
``core.quotepath=false``; the directory branch passed that literal string to
``git hash-object``, which found no such file. Measured 2026-09-05: once
``skills/speechtotext/configs/용어집.example.csv`` landed, every speechtotext deploy
died with ``DEPLOY-BLOCK: cannot hash`` and v1.2.0 left ``SKILL-STALE speechtotext``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_HELPER = _REPO / "automation" / "deploy_provenance.sh"


def _git(repo: Path, *args: str) -> None:
    _ = subprocess.run(
        ("git", "-C", str(repo), *args), check=True, capture_output=True, text=True
    )


def _origin_backed_repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _ = subprocess.run(
        ("git", "init", "--bare", str(origin)), check=True, capture_output=True, text=True
    )
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "config", "user.email", "guard@test.local")
    _git(repo, "config", "user.name", "guard")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "watcher.py").write_text("print('v1')\n", encoding="utf-8")
    _git(repo, "add", "watcher.py")
    _git(repo, "commit", "-m", "add watcher")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    return repo


def _check(repo: Path, path: str) -> subprocess.CompletedProcess[str]:
    script = f'source "{_HELPER}"; deploy_provenance_check "{repo}" "{path}"'
    env = dict(os.environ)
    env.pop("DEPLOY_ALLOW_UNPUSHED", None)
    return subprocess.run(
        ("bash", "-c", script), capture_output=True, text=True, check=False, env=env
    )


def _package_with_non_ascii_file(repo: Path) -> Path:
    package = repo / "pkg"
    package.mkdir()
    (package / "용어집.example.csv").write_text("바른 용어\n", encoding="utf-8")
    (package / "a.py").write_text("a = 1\n", encoding="utf-8")
    _git(repo, "add", "pkg")
    _git(repo, "commit", "-m", "add pkg with a non-ascii filename")
    _git(repo, "push", "origin", "main")
    return package


def test_directory_argument_accepts_a_tracked_non_ascii_filename(tmp_path: Path) -> None:
    repo = _origin_backed_repo(tmp_path)
    package = _package_with_non_ascii_file(repo)

    result = _check(repo, str(package))

    assert result.returncode == 0, result.stderr
    assert "cannot hash" not in result.stderr
    assert "OK: 2 file(s) match" in result.stderr


def test_non_ascii_filename_still_blocks_when_it_is_uncommitted(tmp_path: Path) -> None:
    """The fix must restore legibility, not widen the hole the guard exists to close."""
    repo = _origin_backed_repo(tmp_path)
    package = _package_with_non_ascii_file(repo)
    (package / "용어집.example.csv").write_text("바른 용어\n고친 용어\n", encoding="utf-8")

    result = _check(repo, str(package))

    assert result.returncode == 1, result.stderr
    assert "DEPLOY-BLOCK" in result.stderr
    assert "용어집.example.csv" in result.stderr
