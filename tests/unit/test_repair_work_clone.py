from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from automation.repair import repair_ops_cli
from automation.repair.repair_ops_git import GitRepository
from automation.repair.repair_ops_work_clone import RepairWorkClone


@dataclass(frozen=True, slots=True)
class GitInvocation:
    argv: tuple[str, ...]
    cwd: Path


@dataclass
class RecordingGitRunner:
    invocations: list[GitInvocation] = field(default_factory=list)

    def run(self, argv: tuple[str, ...], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.invocations.append(GitInvocation(argv, cwd))
        stdout = ""
        if argv == ("git", "remote", "get-url", "origin"):
            stdout = "ssh://example.invalid/autophagy.git\n"
        if argv == ("git", "rev-parse", "HEAD"):
            stdout = "repair-commit\n"
        return subprocess.CompletedProcess(argv, 0, stdout, "")


class PreparedWorkClone:
    def __init__(self, deploy_checkout: Path, work_clone: Path) -> None:
        self.deploy_checkout = deploy_checkout
        self.work_clone = work_clone

    def prepare(self) -> Path:
        return self.work_clone


class FakeApproval:
    def permits(self, ticket_id: str, patch_path: Path) -> bool:
        del ticket_id, patch_path
        return True


def _write_patch(path: Path) -> None:
    path.write_text(
        "\n".join(
            (
                "diff --git a/tests/e2e/scenarios/w6-repair-1.yaml b/tests/e2e/scenarios/w6-repair-1.yaml",
                "--- a/tests/e2e/scenarios/w6-repair-1.yaml",
                "+++ b/tests/e2e/scenarios/w6-repair-1.yaml",
            )
        ),
        encoding="utf-8",
    )


def _write_scenario(source: Path, work_clone: Path) -> None:
    driver = work_clone / "tests/e2e/drivers/w4_local.sh"
    driver.parent.mkdir(parents=True)
    driver.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    source.write_text(
        "\n".join(
            (
                "version: 1",
                "id: w6-repair-1",
                "title: Repair work clone regression",
                "driver: tests/e2e/drivers/w4_local.sh",
                "cases:",
                "  - id: repair_path",
                "    kind: happy",
                "    steps:",
                "      - repair uses the isolated clone",
                "    expect:",
                "      work_clone: true",
            )
        ),
        encoding="utf-8",
    )


def _mutating_calls(runner: RecordingGitRunner) -> list[GitInvocation]:
    return [
        invocation
        for invocation in runner.invocations
        if invocation.argv[1] in {"apply", "add", "commit", "revert"}
    ]


def test_repository_when_applying_registering_and_reverting_then_never_mutates_deploy_checkout(tmp_path: Path) -> None:
    # Given: a read-only deploy checkout and a separate repair work clone.
    deploy_checkout = tmp_path / "deploy-checkout"
    work_clone = tmp_path / "repair-work-clone"
    deploy_checkout.mkdir()
    work_clone.mkdir()
    patch = tmp_path / "patch.diff"
    scenario = tmp_path / "scenario.yaml"
    _write_patch(patch)
    _write_scenario(scenario, work_clone)
    runner = RecordingGitRunner()
    repository = GitRepository(work_clone, tmp_path / "bank-state.json", runner)

    # When: the approved repair applies, amends its scenario, and rolls back.
    commit = repository.apply(patch)
    amended = repository.register_bank(scenario)
    repository.revert(amended or commit)

    # Then: every mutating git command has exactly the work-clone cwd, never deploy.
    mutations = _mutating_calls(runner)
    assert [invocation.argv[1] for invocation in mutations] == ["apply", "add", "commit", "add", "commit", "revert"]
    assert all(invocation.cwd == work_clone for invocation in mutations)
    assert all(invocation.cwd != deploy_checkout for invocation in mutations)


def test_repository_when_registering_scenario_then_writes_only_under_work_clone(tmp_path: Path) -> None:
    # Given: scenario roots in both the deploy mirror and the dedicated work clone.
    deploy_checkout = tmp_path / "deploy-checkout"
    work_clone = tmp_path / "repair-work-clone"
    deploy_checkout.mkdir()
    work_clone.mkdir()
    scenario = tmp_path / "scenario.yaml"
    _write_scenario(scenario, work_clone)
    repository = GitRepository(work_clone, tmp_path / "bank-state.json", RecordingGitRunner())

    # When: the repair registers its validated regression scenario.
    _ = repository.register_bank(scenario)

    # Then: the registry writes only within the work clone.
    assert (work_clone / "tests/e2e/scenarios/w6-repair-1.yaml").is_file()
    assert not (deploy_checkout / "tests/e2e/scenarios/w6-repair-1.yaml").exists()


def test_work_clone_when_absent_then_clones_fetches_and_resets_origin_main(tmp_path: Path) -> None:
    # Given: the deploy checkout is readable and the work clone does not exist.
    deploy_checkout = tmp_path / "deploy-checkout"
    work_clone = tmp_path / "repair-work-clone"
    deploy_checkout.mkdir()
    runner = RecordingGitRunner()

    # When: a repair prepares its dedicated work clone.
    prepared = RepairWorkClone(deploy_checkout, work_clone, runner).prepare()

    # Then: it reads origin from deploy but clones and refreshes only the work clone.
    assert prepared == work_clone
    assert runner.invocations == [
        GitInvocation(("git", "remote", "get-url", "origin"), deploy_checkout),
        GitInvocation(("git", "clone", "ssh://example.invalid/autophagy.git", str(work_clone)), tmp_path),
        GitInvocation(("git", "fetch", "origin"), work_clone),
        GitInvocation(("git", "reset", "--hard", "origin/main"), work_clone),
        GitInvocation(("git", "clean", "-fd"), work_clone),
    ]


def test_config_when_work_clone_is_unset_then_uses_dedicated_default(monkeypatch) -> None:
    # Given: no environment override for either repository path.
    monkeypatch.delenv("REPAIR_CHECKOUT", raising=False)
    monkeypatch.delenv("REPAIR_WORK_CLONE", raising=False)

    # When: the ops CLI parses one ticket.
    config = repair_ops_cli._config(["t-repair-1"])

    # Then: deploy and mutation roots are explicit and distinct.
    assert config.checkout == Path("/srv/autophagy-agents")
    assert config.work_clone == Path("/srv/autophagy-repair-work")
    assert config.work_clone != config.checkout


def test_agent_when_constructed_then_routes_writes_to_work_clone_and_sandbox_reads_deploy(
    tmp_path: Path, monkeypatch
) -> None:
    # Given: the CLI has separate deploy and work-clone paths.
    deploy_checkout = tmp_path / "deploy-checkout"
    work_clone = tmp_path / "repair-work-clone"
    config = repair_ops_cli.RepairOpsConfig(
        "t-repair-1",
        deploy_checkout,
        tmp_path / "logs",
        tmp_path / "plans",
        tmp_path / "approvals.jsonl",
        None,
        None,
        work_clone,
    )
    monkeypatch.setattr(repair_ops_cli, "RepairWorkClone", PreparedWorkClone)

    # When: the CLI builds the repair agent.
    agent = repair_ops_cli._agent(config, FakeApproval())

    # Then: writers mutate the work clone while the peer sandbox retains deploy read access.
    assert isinstance(agent.repository, GitRepository)
    assert agent.repository.work_clone == work_clone
    assert agent.patch_docs.docs_root == work_clone / "docs/patch"
    assert agent.sandbox.checkout == deploy_checkout
    assert f"git clone --shared {deploy_checkout}" in agent.sandbox.staged_command(tmp_path / "patch.diff", "true")
