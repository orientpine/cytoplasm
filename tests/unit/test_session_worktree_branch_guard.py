"""세션 워크트리의 main 직접 push만 거부하는 공유 pre-push 훅 회귀."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "automation" / "worktree.sh"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    )


def _try_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=False,
        capture_output=True,
        text=True,
    )


def _identity(repo: Path) -> None:
    _ = _git(repo, "config", "user.email", "session@test.local")
    _ = _git(repo, "config", "user.name", "session")
    _ = _git(repo, "config", "commit.gpgsign", "false")


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, Path, Path]:
    origin = tmp_path / "origin.git"
    _ = subprocess.run(
        ("git", "init", "--quiet", "--bare", "--initial-branch=main", str(origin)),
        check=True,
        capture_output=True,
        text=True,
    )
    main = tmp_path / "main"
    _ = subprocess.run(
        ("git", "clone", "--quiet", str(origin), str(main)),
        check=True,
        capture_output=True,
        text=True,
    )
    _identity(main)
    (main / "state.txt").write_text("seed\n", encoding="utf-8")
    _ = _git(main, "add", "state.txt")
    _ = _git(main, "commit", "--quiet", "-m", "seed")
    _ = _git(main, "push", "--quiet", "-u", "origin", "main")
    return origin, main, tmp_path / "worktrees"


def _start(main: Path, worktree_root: Path, name: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["WORKTREE_ROOT"] = str(worktree_root)
    return subprocess.run(
        ("bash", str(_SCRIPT), "start", name),
        cwd=main,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def _commit(repo: Path, content: str, message: str) -> None:
    (repo / "state.txt").write_text(content, encoding="utf-8")
    _ = _git(repo, "add", "state.txt")
    _ = _git(repo, "commit", "--quiet", "-m", message)


def test_start_installing_twice_leaves_exactly_one_pre_push_hook(
    repository: tuple[Path, Path, Path],
) -> None:
    # Given a main checkout whose common hook directory starts empty
    _, main, worktree_root = repository

    # When two session worktrees are started
    first = _start(main, worktree_root, "s1")
    second = _start(main, worktree_root, "s2")

    # Then the installer replaced one shared path rather than accumulating hooks
    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    hooks = sorted(
        path.name
        for path in (main / ".git" / "hooks").glob("pre-push*")
        if path.suffix != ".sample"
    )
    assert hooks == ["pre-push"]


def test_main_push_from_a_session_worktree_is_refused(
    repository: tuple[Path, Path, Path],
) -> None:
    # Given a linked session worktree with a commit ahead of origin/main
    origin, main, worktree_root = repository
    start = _start(main, worktree_root, "s1")
    assert start.returncode == 0, start.stdout + start.stderr
    session = worktree_root / "s1"
    _identity(session)
    _commit(session, "session change\n", "session change")
    before = _git(origin, "rev-parse", "refs/heads/main").stdout.strip()

    # When that session tries to bypass PR review by updating main directly
    push = _try_git(session, "push", "origin", "HEAD:refs/heads/main")

    # Then the hook refuses the push and the remote ref does not move
    assert push.returncode != 0, push.stdout + push.stderr
    assert _git(origin, "rev-parse", "refs/heads/main").stdout.strip() == before


def test_main_push_from_the_main_checkout_still_passes(
    repository: tuple[Path, Path, Path],
) -> None:
    # Given the same shared hook installed by starting a session worktree
    origin, main, worktree_root = repository
    start = _start(main, worktree_root, "s1")
    assert start.returncode == 0, start.stdout + start.stderr
    _commit(main, "landed change\n", "landed change")

    # When the main checkout follows the land.sh push path
    push = _try_git(main, "push", "origin", "main")

    # Then the hook permits it and origin/main advances
    assert push.returncode == 0, push.stdout + push.stderr
    assert _git(origin, "rev-parse", "refs/heads/main").stdout.strip() == _git(
        main, "rev-parse", "HEAD"
    ).stdout.strip()
