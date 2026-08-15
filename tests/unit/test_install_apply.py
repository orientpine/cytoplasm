from __future__ import annotations

from pathlib import Path
import base64
import subprocess
from dataclasses import replace

import pytest

from automation.install.apply import apply_plan
from automation.install.assets import build_inputs, render_plan
from automation.install.checks import CheckResult, Status
from automation.install.executor import ExecutionContext, RealExecutor
from automation.install.plan import (
    Check,
    EnsureAccount,
    InstallAction,
    InstallPlan,
    SystemState,
    build_plan,
)
from automation.install.installer import main
from automation.node_config import default_node_config


class RecordingExecutor:
    results: dict[str, Status]
    executed: list[InstallAction]

    def __init__(self, results: dict[str, Status]) -> None:
        self.results = results
        self.executed = []

    def execute(self, action: InstallAction) -> tuple[CheckResult, ...]:
        self.executed.append(action)
        name = action.name if isinstance(action, (Check, EnsureAccount)) else type(action).__name__
        status = self.results.get(name, Status.PASS)
        return (CheckResult(name, status, "test result"),)


def test_apply_executes_in_plan_order_and_reaches_healthcheck_last() -> None:
    plan = InstallPlan((EnsureAccount("agent", Path("/home/agent")), Check("healthcheck")))
    executor = RecordingExecutor({})

    results = apply_plan(plan, executor)

    assert executor.executed == list(plan.actions)
    assert [result.status for result in results] == [Status.PASS, Status.PASS]


def test_apply_stops_at_first_failure() -> None:
    plan = InstallPlan((Check("hermes-gateway"), Check("discord-readiness"), Check("healthcheck")))
    executor = RecordingExecutor({"discord-readiness": Status.FAIL})

    results = apply_plan(plan, executor)

    assert executor.executed == [Check("hermes-gateway"), Check("discord-readiness")]
    assert results[-1].status is Status.FAIL


def test_dry_run_render_contains_targets_but_not_file_contents() -> None:
    plan = InstallPlan((EnsureAccount("member", Path("/home/member")), Check("healthcheck")))

    rendered = render_plan(plan)

    assert "account member" in rendered
    assert "check healthcheck" in rendered


def test_cli_dry_run_is_read_only_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    algorithm = b"ssh-ed25519"
    blob = len(algorithm).to_bytes(4, "big") + algorithm
    blob += (32).to_bytes(4, "big") + bytes(range(32))
    key = tmp_path / "update.pub"
    _ = key.write_text(
        f"ssh-ed25519 {base64.b64encode(blob).decode()} dry-run\n",
        encoding="utf-8",
    )

    code = main(("--update-trust-key", str(key), "--dry-run"))

    assert code == 0
    output = capsys.readouterr().out
    assert "INSTALL PLAN" in output
    assert "check healthcheck" in output


def test_missing_hermes_gateway_fails_closed_with_external_prerequisite_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = default_node_config()
    context = ExecutionContext(config, Path.cwd(), Path("/absent"), None)
    executor = RealExecutor(context)

    def fail_after_identity(
        command: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del env, cwd
        if command[:2] == ("id", "-u"):
            return subprocess.CompletedProcess(command, 0, "1000\n", "")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(executor, "_run", fail_after_identity)

    (result,) = executor.execute(Check("hermes-gateway"))

    assert result.status is Status.FAIL
    assert "external prerequisite" in result.detail
    assert "never installs Hermes" in result.detail


def test_deploy_key_registration_when_key_exists_then_prints_exact_copyable_public_key(
    tmp_path: Path,
) -> None:
    # Given
    config = replace(default_node_config(), ops_home=tmp_path / "ops")
    public_path = config.ops_home / ".ssh" / "id_ed25519.pub"
    public_path.parent.mkdir(parents=True)
    algorithm = b"ssh-ed25519"
    blob = len(algorithm).to_bytes(4, "big") + algorithm
    blob += (32).to_bytes(4, "big") + bytes(range(32))
    public_key = f"ssh-ed25519 {base64.b64encode(blob).decode()} member-node-deploy"
    _ = public_path.write_text(f"{public_key}\n", encoding="utf-8")
    executor = RealExecutor(ExecutionContext(config, Path.cwd(), Path("/absent"), None))

    # When
    (result,) = executor.execute(Check("deploy-key-registration"))

    # Then
    assert result.status is Status.PASS
    assert "GROUP-JOIN-DEPLOY-PUBLIC-KEY" in result.detail
    assert result.detail.count(public_key) == 1
    assert "read-only" in result.detail
    assert "group admin" in result.detail
    assert "out-of-band" in result.detail
    assert "GROUP-DISCORD-FORBIDDEN" in result.detail


def test_injected_install_reaches_same_state_on_second_run() -> None:
    config = default_node_config()
    algorithm = b"ssh-ed25519"
    blob = len(algorithm).to_bytes(4, "big") + algorithm
    blob += (32).to_bytes(4, "big") + bytes(range(32))
    key = f"ssh-ed25519 {base64.b64encode(blob).decode()} integration"
    inputs = build_inputs(Path.cwd(), config, key)
    first = build_plan(inputs, SystemState.empty())
    executor = RecordingExecutor({})

    first_results = apply_plan(first, executor)
    final_state = SystemState.from_actions(first.actions)
    second = build_plan(inputs, final_state)

    assert all(result.status is Status.PASS for result in first_results)
    assert all(isinstance(action, Check) for action in second.actions)
