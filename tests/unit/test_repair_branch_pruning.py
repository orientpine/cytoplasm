"""Prune only disposable completed-ticket local refs, using real git repositories."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.repair import repair_ops_cli as cli
from automation.repair.repair_ops_approval import ManualOwnerApproval
from automation.repair.repair_ops_core import RepairOutcome, RepairPhase
from automation.repair.repair_ops_git import RepairOpsError
from automation.repair.repair_ops_work_clone import RepairWorkClone
from tests.unit.test_repair_ops_residuals import OutcomeAgent, config_at
from tests.unit import test_repair_patch_apply_residuals as git_fixtures
from tests.unit.test_repair_patch_apply_residuals import git

repository = git_fixtures.repository


@pytest.fixture
def clone(repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RepairWorkClone:
    executable = tmp_path / "bin/hermes"
    executable.parent.mkdir()
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "assert sys.argv[1:3] == ['kanban', 'show']\n"
        "status = json.loads(os.environ['CARD_STATES'])[sys.argv[3]]\n"
        "print(json.dumps({'task': {'status': status}, 'events': []}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable.parent}:/usr/bin:/bin")
    monkeypatch.setenv("CARD_STATES", json.dumps({"t_abcdef": "done", "t_123456": "archived", "t_654321": "running"}))
    git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    return RepairWorkClone(repository, repository)


def test_prune_removes_only_local_refs_when_cards_are_terminal(clone: RepairWorkClone) -> None:
    # Given: completed and live ticket refs, plus protected backup and remote refs.
    root = clone.work_clone
    removable = [f"{prefix}/topic-t_abcdef" for prefix in ("task", "kanban", "wt", "fix", "repair")]
    removable.append("task/t_123456")
    retained = ["task/t_654321", "backup/t_abcdef", "pre-realign-t_abcdef", "fix/no-ticket", "fix/t_abcdef-t_654321"]
    for branch in removable + retained:
        git(root, "branch", branch)
    git(root, "update-ref", "refs/remotes/origin/repair/t_abcdef", "HEAD")
    # When: publication has succeeded and local garbage collection runs.
    removed = clone.prune_completed_branches()
    # Then: all five managed prefixes are cleaned, but no remote/protected ref changes.
    assert set(removed) == set(removable)
    assert set(git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()) == {"main", *retained}
    assert git(root, "rev-parse", "refs/remotes/origin/repair/t_abcdef") == git(root, "rev-parse", "HEAD")


def test_prune_keeps_refs_when_unmerged_or_checked_out(clone: RepairWorkClone, tmp_path: Path) -> None:
    # Given: done cards are not proof that local work can be destroyed.
    root = clone.work_clone
    git(root, "branch", "task/t_abcdef")
    git(root, "worktree", "add", str(tmp_path / "linked"), "task/t_abcdef")
    git(root, "switch", "-c", "fix/t_abcdef")
    (root / "new.txt").write_text("unpublished\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "test: unmerged work")
    git(root, "branch", "repair/t_123456")
    # When: cleanup considers checked-out and unmerged refs.
    removed = clone.prune_completed_branches()
    # Then: it preserves both working trees and the unmerged tip.
    assert removed == ()
    assert set(git(root, "branch", "--format=%(refname:short)").splitlines()) == {"main", "task/t_abcdef", "fix/t_abcdef", "repair/t_123456"}


def test_prune_refuses_when_card_status_is_unreadable(clone: RepairWorkClone, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: kanban cannot resolve a candidate card.
    git(clone.work_clone, "branch", "task/t_abcdef")
    monkeypatch.setenv("CARD_STATES", "{}")
    # When / Then: uncertainty never authorizes deletion.
    with pytest.raises(RepairOpsError, match="card"):
        clone.prune_completed_branches()
    assert git(clone.work_clone, "show-ref", "--verify", "refs/heads/task/t_abcdef")


@pytest.mark.parametrize("published", [False, True])
def test_cli_prunes_only_when_push_and_pr_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published: bool,
) -> None:
    # Given: an applied repair with a successful push but uncertain PR publication.
    config = config_at(tmp_path)
    outcome = RepairOutcome(RepairPhase.COMPLETED, config.ticket_id, "abc", None)
    calls: list[str] = []
    monkeypatch.setattr(cli, "_agent", lambda *args: OutcomeAgent(outcome))
    monkeypatch.setattr(cli, "private_log", lambda *args: "")
    monkeypatch.setattr(cli, "_push_repair_branch", lambda *args: "repair/t_abcdef")

    def publish(self: RepairWorkClone, ticket_id: str) -> str:
        if not published:
            raise RepairOpsError("gh unavailable")
        return "https://example.invalid/pull/1"

    def prune(self: RepairWorkClone) -> tuple[str, ...]:
        calls.append("prune")
        return ()

    monkeypatch.setattr(RepairWorkClone, "ensure_pull_request", publish)
    monkeypatch.setattr(RepairWorkClone, "prune_completed_branches", prune, raising=False)
    # When: the CLI completes publication.
    cli._run(config, ManualOwnerApproval("owner", None))
    # Then: failed publication cannot trigger local cleanup.
    assert calls == (["prune"] if published else [])
