"""PR publication and child-output contracts, without GitHub or Discord effects."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from automation.repair import repair_ops_cli as cli
from automation.repair.repair_ops_approval import ManualOwnerApproval
from automation.repair.repair_ops_core import RepairOutcome, RepairPhase
from automation.repair.repair_ops_git import RepairOpsError
from automation.repair.repair_ops_reaction_watch import CliRepairApprovalCommands
from automation.repair.repair_ops_work_clone import RepairWorkClone
from tests.unit.test_repair_ops_residuals import OutcomeAgent, config_at
from tests.unit import test_repair_patch_apply_residuals as git_fixtures

repository = git_fixtures.repository


@dataclass(frozen=True, slots=True)
class GhSandbox:
    clone: RepairWorkClone
    calls: Path


@pytest.fixture
def github(repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GhSandbox:
    executable = tmp_path / "bin" / "gh"
    executable.parent.mkdir()
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['GH_CALLS'], 'a') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if os.environ.get('GH_FAIL'):\n"
        "    print('authentication required', file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "if sys.argv[2] == 'list':\n"
        "    print(os.environ.get('GH_EXISTING', ''))\n"
        "else:\n"
        "    print('https://example.invalid/pull/1')\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable.parent}:/usr/bin:/bin")
    calls = tmp_path / "gh-calls.jsonl"
    monkeypatch.setenv("GH_CALLS", str(calls))
    monkeypatch.delenv("GH_FAIL", raising=False)
    monkeypatch.delenv("GH_EXISTING", raising=False)
    return GhSandbox(RepairWorkClone(repository, repository), calls)


@pytest.mark.parametrize("existing", [False, True])
def test_pr_is_created_or_reused_when_branch_was_pushed(
    github: GhSandbox, monkeypatch: pytest.MonkeyPatch, existing: bool,
) -> None:
    # Given: the real git commit supplies the title; gh is a sandbox executable.
    url = "https://example.invalid/pull/1"
    if existing:
        monkeypatch.setenv("GH_EXISTING", url)
    # When: publication reaches its PR endpoint.
    actual = github.clone.ensure_pull_request("t_abcdef")
    # Then: only this repair head targets main, and an open PR is reused.
    assert actual == url
    calls = [json.loads(line) for line in github.calls.read_text().splitlines()]
    assert calls[0][0:2] == ["pr", "list"]
    assert calls[0][calls[0].index("--head") + 1] == "repair/t_abcdef"
    assert calls[0][calls[0].index("--base") + 1] == "main"
    assert calls[0][calls[0].index("--state") + 1] == "open"
    assert len(calls) == (1 if existing else 2)
    if not existing:
        create = calls[1]
        assert create[0:2] == ["pr", "create"]
        assert create[create.index("--title") + 1] == "test: seed"
        assert create[create.index("--head") + 1] == "repair/t_abcdef"
        assert create[create.index("--base") + 1] == "main"
        assert "t_abcdef" in create[create.index("--body") + 1]


def test_pr_refuses_when_gh_is_unauthenticated(github: GhSandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: gh cannot authenticate with its sandbox credential.
    monkeypatch.setenv("GH_FAIL", "1")
    # When / Then: the publication error is explicit, not a missing URL success.
    with pytest.raises(RepairOpsError, match="authentication required"):
        github.clone.ensure_pull_request("t_abcdef")


def test_pr_refuses_when_gh_is_missing(github: GhSandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the subprocess boundary cannot find the gh executable.
    def missing(*args: str, **kwargs: str) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", missing)
    # When / Then: the absent dependency has a named operational error.
    with pytest.raises(RepairOpsError, match="gh"):
        github.clone.ensure_pull_request("t_abcdef")


@pytest.mark.parametrize("failed", [False, True])
def test_cli_surfaces_pr_outcome_when_push_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failed: bool,
) -> None:
    # Given: an applied commit reached its repair branch.
    config = config_at(tmp_path)
    outcome = RepairOutcome(RepairPhase.COMPLETED, config.ticket_id, "abc", None)
    monkeypatch.setattr(cli, "_agent", lambda *args: OutcomeAgent(outcome))
    monkeypatch.setattr(cli, "private_log", lambda *args: "")
    monkeypatch.setattr(cli, "_push_repair_branch", lambda *args: "repair/t_abcdef")

    def publish(self: RepairWorkClone, ticket_id: str) -> str:
        if failed:
            raise RepairOpsError("gh unavailable")
        return "https://example.invalid/pull/1"

    monkeypatch.setattr(RepairWorkClone, "ensure_pull_request", publish, raising=False)
    # When: the CLI serializes the publication result.
    cli._run(config, ManualOwnerApproval("owner", None))
    # Then: callers can distinguish PR creation from an applied-but-unpublished PR.
    payload = json.loads(capsys.readouterr().out)
    assert payload["pr_url"] == (None if failed else "https://example.invalid/pull/1")
    assert payload["pr_error"] == ("gh unavailable" if failed else None)


def test_watcher_preserves_pr_result_when_child_reports_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Given: a real child CLI returns a machine-readable publication failure.
    script = tmp_path / "automation/repair/child.py"
    script.parent.mkdir(parents=True)
    payload = {"pr_url": None, "pr_error": "gh unavailable"}
    script.write_text(f"print({json.dumps(payload)!r})\n", encoding="utf-8")
    # When: the watcher executes the child instead of swallowing its stdout.
    CliRepairApprovalCommands(script, "synthetic")._run("--apply-approved", "t_abcdef")
    # Then: the journal-facing stdout still contains the explicit PR outcome.
    assert json.loads(capsys.readouterr().out) == payload
