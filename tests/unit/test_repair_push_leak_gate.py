"""A repair branch is pushed to a public origin only after the public leak gate passes.

Repairs are born from production observations, so they are the likeliest path for an
installation's hosts or addresses to reach the public repository. Kept in its own file:
the older repair push test files are pinned by FS3 replay hashes.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation.repair.repair_ops_git import RepairOpsError, SubprocessGitRunner
from automation.repair.repair_ops_work_clone import RepairWorkClone

_TAILNET = ".".join(("100", "101", "7", "9"))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(repo), *args), check=True, capture_output=True)


class _RecordingRunner:
    def __init__(self) -> None:
        self.pushes: list[tuple[str, ...]] = []
        self._real = SubprocessGitRunner()

    def run(self, argv: tuple[str, ...], *, cwd: Path, input: bytes | None = None) -> subprocess.CompletedProcess[str]:  # noqa: A002
        if "push" in argv:
            self.pushes.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return self._real.run(argv, cwd=cwd, input=input)


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "code.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@test.invalid", "commit", "-q", "-m", "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def _repair(repo: Path, text: str) -> None:
    (repo / "code.py").write_text(text, encoding="utf-8")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@test.invalid", "commit", "-qam", "fix: repair")


def test_clean_repair_is_pushed(clone: Path, tmp_path: Path) -> None:
    _repair(clone, "VALUE = 2\n")
    runner = _RecordingRunner()

    assert RepairWorkClone(tmp_path, clone, runner).push_branch("t_abc123") == "repair/t_abc123"
    assert len(runner.pushes) == 1


def test_repair_carrying_installation_topology_is_not_pushed(clone: Path, tmp_path: Path) -> None:
    _repair(clone, f"HOST = '{_TAILNET}'\n")
    runner = _RecordingRunner()

    with pytest.raises(RepairOpsError, match="public leak gate refused"):
        RepairWorkClone(tmp_path, clone, runner).push_branch("t_abc123")
    assert runner.pushes == []
