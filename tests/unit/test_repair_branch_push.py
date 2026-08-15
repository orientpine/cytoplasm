"""Rollout ③: the repair agent publishes to a branch, never to main.

「수리 반영 경로 규칙」(root AGENTS.md, 2026-07-29): the work clone pushes
`repair/t_<ticket>` with a repository-scoped write deploy key, and main is
merged by cha on GitHub. A direct push to main — or an auto fast-forward —
would put unreviewed automated code on the branch every node pulls from.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from automation.repair.repair_ops_git import RepairOpsError
from automation.repair.repair_ops_work_clone import RepairWorkClone


@dataclass
class _FakeRunner:
    calls: list[tuple[tuple[str, ...], Path]] = field(default_factory=list)
    fail_on: str | None = None

    def run(self, argv: tuple[str, ...], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, cwd))
        if self.fail_on and self.fail_on in " ".join(argv):
            return subprocess.CompletedProcess(argv, 1, "", "boom")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def pushes(self) -> list[tuple[str, ...]]:
        return [a for a, _ in self.calls if "push" in a]


def _clone(tmp_path: Path, runner: _FakeRunner) -> RepairWorkClone:
    return RepairWorkClone(tmp_path / "deploy", tmp_path / "work", runner)


def test_push_branch_when_given_ticket_then_targets_only_the_repair_branch(tmp_path: Path) -> None:
    runner = _FakeRunner()
    ref = _clone(tmp_path, runner).push_branch("t_abc123")

    assert ref == "repair/t_abc123"
    pushed = runner.pushes()
    assert len(pushed) == 1, f"expected exactly one push, got {pushed}"
    argv = pushed[0]
    assert "HEAD:refs/heads/repair/t_abc123" in argv
    # main must never be a push target, in any form.
    joined = " ".join(argv)
    assert "refs/heads/main" not in joined
    assert not any(part == "main" for part in argv)


def test_push_branch_when_ticket_is_not_a_ticket_id_then_refuses(tmp_path: Path) -> None:
    runner = _FakeRunner()
    clone = _clone(tmp_path, runner)
    for bad in ("main", "../main", "t_abc/../main", "", "refs/heads/main"):
        with pytest.raises(RepairOpsError):
            clone.push_branch(bad)
    assert runner.pushes() == [], "a rejected ticket id must not reach git push"


def test_push_branch_when_push_fails_then_raises_and_does_not_claim_success(
    tmp_path: Path,
) -> None:
    runner = _FakeRunner(fail_on="push")
    with pytest.raises(RepairOpsError):
        _clone(tmp_path, runner).push_branch("t_abc123")


def test_push_branch_when_pushing_then_uses_the_repair_write_key(tmp_path: Path) -> None:
    runner = _FakeRunner()
    _clone(tmp_path, runner).push_branch("t_abc123", ssh_key=Path("/home/ops/.ssh/repair_push_key"))
    argv = runner.pushes()[0]
    # The key is carried by GIT_SSH_COMMAND via -c core.sshCommand so the ops
    # read-only key is never the one used for a write.
    assert any("repair_push_key" in part for part in argv), argv
