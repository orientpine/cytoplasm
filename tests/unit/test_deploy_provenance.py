"""Deploy guard: prod must never receive code that is absent from the deploy reference.

Every deploy script copies files from the LOCAL checkout to the agent host. With
parallel sessions a file is easily pushed to prod while still uncommitted, or
committed but never pushed — prod then runs code that is not in origin/main, and the
next deploy from any clean checkout silently reverts it (2026-07-25 선례:
mail_digest_watch.py DNS-retry fix). ``deploy_provenance_check`` compares the
working-tree blob of each deployed path against the same path in the deploy
reference, so one comparison catches both "not committed" and "not pushed".
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
    """A work repo whose committed+pushed state is exactly one tracked file."""
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


def _check(repo: Path, *paths: str, allow_unpushed: bool = False) -> subprocess.CompletedProcess[str]:
    quoted = " ".join(f'"{path}"' for path in paths)
    script = f'source "{_HELPER}"; deploy_provenance_check "{repo}" {quoted}'
    env = dict(os.environ)
    if allow_unpushed:
        env["DEPLOY_ALLOW_UNPUSHED"] = "1"
    else:
        env.pop("DEPLOY_ALLOW_UNPUSHED", None)
    return subprocess.run(
        ("bash", "-c", script), capture_output=True, text=True, check=False, env=env
    )


def test_committed_and_pushed_file_is_allowed(tmp_path: Path) -> None:
    repo = _origin_backed_repo(tmp_path)
    result = _check(repo, str(repo / "watcher.py"))
    assert result.returncode == 0, result.stderr
    assert "OK: 1 file(s) match origin/main" in result.stderr


def test_uncommitted_edit_is_blocked(tmp_path: Path) -> None:
    repo = _origin_backed_repo(tmp_path)
    (repo / "watcher.py").write_text("print('hotfix')\n", encoding="utf-8")
    result = _check(repo, str(repo / "watcher.py"))
    assert result.returncode == 1
    assert "DEPLOY-BLOCK" in result.stderr and "commit and push" in result.stderr


def test_committed_but_unpushed_edit_is_blocked(tmp_path: Path) -> None:
    """The exact shape that silently reverts: local commit that origin/main lacks."""
    repo = _origin_backed_repo(tmp_path)
    (repo / "watcher.py").write_text("print('hotfix')\n", encoding="utf-8")
    _git(repo, "commit", "-am", "local-only hotfix")
    result = _check(repo, str(repo / "watcher.py"))
    assert result.returncode == 1
    assert "DEPLOY-BLOCK" in result.stderr


def test_untracked_file_is_blocked(tmp_path: Path) -> None:
    repo = _origin_backed_repo(tmp_path)
    (repo / "new_watcher.py").write_text("print('new')\n", encoding="utf-8")
    result = _check(repo, str(repo / "new_watcher.py"))
    assert result.returncode == 1
    assert "untracked" in result.stderr


def test_directory_argument_checks_every_tracked_file(tmp_path: Path) -> None:
    repo = _origin_backed_repo(tmp_path)
    package = repo / "pkg"
    package.mkdir()
    (package / "a.py").write_text("a = 1\n", encoding="utf-8")
    (package / "b.py").write_text("b = 1\n", encoding="utf-8")
    _git(repo, "add", "pkg")
    _git(repo, "commit", "-m", "add pkg")
    _git(repo, "push", "origin", "main")
    assert _check(repo, str(package)).returncode == 0

    (package / "b.py").write_text("b = 2\n", encoding="utf-8")
    blocked = _check(repo, str(package))
    assert blocked.returncode == 1
    assert "pkg/b.py" in blocked.stderr

def test_directory_argument_blocks_untracked_files(tmp_path: Path) -> None:
    """Callers ship the WHOLE directory (tar/rsync), not just its tracked files.

    Enumerating only ``git ls-files`` made the guard report OK for a directory
    holding brand-new code, which would then reach prod having bypassed commit,
    the deploy reference and every review gate (measured on
    automation/hermes_compat: "OK: 17 file(s) match origin/main", rc 0, with an
    untracked .py inside).
    """
    repo = _origin_backed_repo(tmp_path)
    package = repo / "pkg"
    package.mkdir()
    (package / "a.py").write_text("a = 1\n", encoding="utf-8")
    _git(repo, "add", "pkg")
    _git(repo, "commit", "-m", "add pkg")
    _git(repo, "push", "origin", "main")
    (package / "rogue.py").write_text("print('never committed')\n", encoding="utf-8")

    result = _check(repo, str(package))

    assert result.returncode == 1, result.stderr
    assert "DEPLOY-BLOCK" in result.stderr
    assert "pkg/rogue.py" in result.stderr


def test_directory_argument_ignores_gitignored_build_residue(tmp_path: Path) -> None:
    """__pycache__/.venv are declared non-source; blocking on them breaks every deploy."""
    repo = _origin_backed_repo(tmp_path)
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n.venv/\n", encoding="utf-8")
    package = repo / "pkg"
    package.mkdir()
    (package / "a.py").write_text("a = 1\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "pkg")
    _git(repo, "commit", "-m", "add pkg")
    _git(repo, "push", "origin", "main")
    cache = package / "__pycache__"
    cache.mkdir()
    (cache / "a.cpython-312.pyc").write_bytes(b"\x00compiled")

    result = _check(repo, str(package))

    assert result.returncode == 0, result.stderr


def test_override_still_bypasses_the_untracked_directory_check(tmp_path: Path) -> None:
    """DEPLOY_ALLOW_UNPUSHED=1 stays the sandbox escape hatch for directories too."""
    repo = _origin_backed_repo(tmp_path)
    package = repo / "pkg"
    package.mkdir()
    (package / "a.py").write_text("a = 1\n", encoding="utf-8")
    _git(repo, "add", "pkg")
    _git(repo, "commit", "-m", "add pkg")
    _git(repo, "push", "origin", "main")
    (package / "rogue.py").write_text("sandbox\n", encoding="utf-8")

    result = _check(repo, str(package), allow_unpushed=True)

    assert result.returncode == 0, result.stderr


def test_explicit_override_allows_sandbox_deploys(tmp_path: Path) -> None:
    repo = _origin_backed_repo(tmp_path)
    (repo / "watcher.py").write_text("print('sandbox')\n", encoding="utf-8")
    result = _check(repo, str(repo / "watcher.py"), allow_unpushed=True)
    assert result.returncode == 0
    assert "DEPLOY_ALLOW_UNPUSHED=1" in result.stderr


def test_missing_deploy_reference_fails_closed(tmp_path: Path) -> None:
    repo = tmp_path / "solo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "guard@test.local")
    _git(repo, "config", "user.name", "guard")
    (repo / "watcher.py").write_text("print('v1')\n", encoding="utf-8")
    _git(repo, "add", "watcher.py")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "commit", "-m", "add watcher")
    result = _check(repo, str(repo / "watcher.py"))  # no origin remote at all
    assert result.returncode == 1
    assert "deploy reference origin/main is unavailable" in result.stderr
