"""보고 허브 선택·수렴·거부 경계의 회귀 검사."""
from __future__ import annotations

import base64
import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest

from automation.install import plan as planning
from automation.install.assets import build_inputs, render_plan
from automation.install.components import UnknownComponentError, resolve_components
from automation.install.installer import main
from automation.node_config import default_node_config

REPO: Final = Path(__file__).resolve().parents[2]
UNITS: Final = ("report-hub-collector.service", "report-hub-dashboard.service")
ENV_NAMES: Final = {
    "DISCORD_BOT_TOKEN", "REPORT_HUB_GUILD_ID", "REPORT_HUB_CHANNEL_NAME",
    "REPORT_HUB_DB", "REPORT_HUB_QUARANTINE_LOG", "REPORT_HUB_PEERS_FILE",
    "REPORT_HUB_POLL_SECONDS", "REPORT_HUB_BIND_HOST", "REPORT_HUB_BIND_PORT",
    "REPORT_HUB_DASHBOARD_USER", "REPORT_HUB_DASHBOARD_PASSWORD_SHA256",
}


@pytest.fixture
def trust() -> str:
    algorithm = b"ssh-ed25519"
    material = len(algorithm).to_bytes(4, "big") + algorithm
    material += (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(material).decode()} report-hub-test"


@pytest.mark.parametrize("profile", (None, "core", "rag"))
def test_component_is_absent_when_unselected(trust: str, profile: str | None) -> None:
    # Given
    inputs = build_inputs(REPO, default_node_config(), trust, profile=profile)
    # When
    plan = planning.build_plan(inputs, planning.SystemState.empty())
    # Then
    assert "report-hub" not in render_plan(plan)


@pytest.mark.parametrize("profile", ("report-hub", "full"))
def test_user_assets_are_planned_when_profile_selects_hub(trust: str, profile: str) -> None:
    # Given
    config = replace(default_node_config(), ops_home=Path("/home/hub-ops"))
    inputs = build_inputs(REPO, config, trust, profile=profile)
    # When
    plan = planning.build_plan(inputs, planning.SystemState.empty())
    # Then
    files = {action.spec.path: action.spec for action in plan.actions
             if isinstance(action, planning.EnsureFile)}
    for name in UNITS:
        unit = files[config.ops_home / ".config/systemd/user" / name]
        assert (unit.owner, unit.group, unit.mode) == (config.ops_account, config.ops_account, 0o644)
        assert f"WorkingDirectory={config.ops_home}/report-hub" in unit.content
        assert "$NODE_" not in unit.content
        assert name not in inputs.timers
    hub = config.ops_home / "report-hub"
    env = files[hub / "hub.env"]
    assert (env.owner, env.group, env.mode, env.create_only) == (
        config.ops_account, config.ops_account, 0o600, True,
    )
    assignments = dict(line.removeprefix("# ").split("=", 1)
                       for line in env.content.splitlines() if "=" in line)
    assert assignments == dict.fromkeys(ENV_NAMES, "")
    directories = {action.spec.path: action.spec for action in plan.actions
                   if isinstance(action, planning.EnsureDirectory)}
    assert directories[hub].mode == 0o750
    assert directories[hub].owner == config.ops_account
    assert directories[config.ops_home / ".config/systemd/user"].owner == config.ops_account
    links = [action for action in plan.actions if isinstance(action, planning.EnsureSymlink)]
    assert links == [planning.EnsureSymlink(hub / "automation", config.release_current / "automation", config.ops_account)]
    enables = [action for action in plan.actions if isinstance(action, planning.EnableUserUnit)]
    assert enables == [planning.EnableUserUnit(name, config.ops_account) for name in UNITS]


def test_second_run_is_check_only_when_hub_converged(trust: str) -> None:
    # Given
    inputs = build_inputs(REPO, default_node_config(), trust, profile="report-hub")
    first = planning.build_plan(inputs, planning.SystemState.empty())
    state = planning.SystemState.from_actions(first.actions)
    # When
    second = planning.build_plan(inputs, state)
    # Then
    assert all(isinstance(action, planning.Check) for action in second.actions)


def test_credentials_are_preserved_when_operator_edits_template(trust: str) -> None:
    # Given
    inputs = build_inputs(REPO, default_node_config(), trust, profile="report-hub")
    state = planning.SystemState.from_actions(planning.build_plan(inputs, planning.SystemState.empty()).actions)
    env_path = inputs.config.ops_home / "report-hub/hub.env"
    state = replace(state, files={**state.files, env_path: planning.FileState("operator-edited", 0o600, "ops", "ops")})
    # When
    second = planning.build_plan(inputs, state)
    # Then
    assert all(isinstance(action, planning.Check) for action in second.actions)


@pytest.mark.parametrize("flags", (
    ("--profile", "report-hub"),
    ("--profile", "core", "--with-component", "report-hub"),
    ("--profile", "full", "--with-component", "report-hub", "--with-component", "report-hub"),
))
def test_cli_plans_user_units_when_hub_selected(
    trust: str, tmp_path: Path, flags: tuple[str, ...],
) -> None:
    # Given
    key = tmp_path / "trust.pub"
    _ = key.write_text(trust, encoding="utf-8")
    # When
    result = subprocess.run(
        ("python3", "-m", "automation.install", "--update-trust-key", str(key), "--dry-run", *flags),
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    # Then
    out = result.stdout
    assert result.returncode == 0, result.stderr
    for name in UNITS:
        assert out.count(f"file /home/ops/.config/systemd/user/{name} ") == 1
        assert out.count(f"user-unit {name} ") == 1
    assert "file /home/ops/report-hub/hub.env " in out
    assert "directory /home/ops/report-hub " in out
    assert "symlink /home/ops/report-hub/automation " in out


def test_unknown_component_is_refused_when_registry_cannot_resolve_it() -> None:
    # Given
    name = "report-hbu"
    # When
    with pytest.raises(UnknownComponentError) as error:
        _ = resolve_components((name,))
    # Then
    assert all(known in str(error.value) for known in (name, "managed-sync", "report-hub"))


def test_cli_refuses_unknown_component_when_operator_mistypes(capsys: pytest.CaptureFixture[str]) -> None:
    # Given
    args = ("--update-trust-key", "/unused", "--dry-run", "--with-component", "report-hbu")
    # When
    with pytest.raises(SystemExit) as error:
        _ = main(args)
    # Then
    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert all(name in stderr for name in ("report-hbu", "managed-sync", "report-hub"))


@pytest.mark.parametrize("name", UNITS)
def test_manual_unit_bytes_are_preserved_when_installer_renders(trust: str, name: str) -> None:
    # Given
    relative = Path("automation/report_hub/systemd") / name
    original = subprocess.check_output(("git", "show", f"HEAD:{relative}"), cwd=REPO)
    # When
    _ = build_inputs(REPO, default_node_config(), trust, profile="report-hub")
    # Then
    assert hashlib.sha256((REPO / relative).read_bytes()).digest() == hashlib.sha256(original).digest()
