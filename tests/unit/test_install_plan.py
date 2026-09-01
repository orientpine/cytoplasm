from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from pathlib import Path

from automation.install.assets import build_inputs
from automation.install.plan import (
    Check,
    DirectoryState,
    EnableTimer,
    EnsureAccount,
    EnsureDirectory,
    EnsureFile,
    EnsureGroup,
    EnsureRepository,
    FileSpec,
    FileState,
    GenerateDeployKey,
    InstallInputs,
    SystemState,
    build_plan,
)
from automation.node_config import default_node_config, load_node_config


_REPO = Path(__file__).resolve().parents[2]


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _public_key() -> str:
    algorithm = b"ssh-ed25519"
    material = len(algorithm).to_bytes(4, "big") + algorithm
    material += (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(material).decode()} installer-test"


def _inputs() -> InstallInputs:
    config = replace(
        default_node_config(),
        agent_account="member-agent",
        peer_account="member-peer",
        ops_account="member-ops",
        service_group="member-services",
        agent_home=Path("/home/member-agent"),
        peer_home=Path("/home/member-peer"),
        ops_home=Path("/home/member-ops"),
        service_root=Path("/opt/member"),
        deploy_checkout=Path("/opt/member/checkout"),
        release_current=Path("/opt/member/current"),
        release_store=Path("/opt/member/releases"),
        private_root=Path("/opt/member/private"),
        skill_store=Path("/opt/member/skills"),
        repair_work=Path("/opt/member/repair"),
        repair_report_queue=Path("/opt/member/report-queue"),
        repair_report_ack=Path("/opt/member/report-ack"),
        repair_capability=Path("/opt/member/capability"),
        libexec_dir=Path("/opt/member/libexec"),
    )
    return InstallInputs(
        config=config,
        files=(
            FileSpec(
                Path("/etc/systemd/system/autophagy-deploy-reconcile.service"),
                "rendered-service\n",
                0o644,
                "root",
                "root",
            ),
        ),
        timers=("autophagy-deploy-reconcile.timer",),
    )


def test_empty_system_plans_every_resource_and_terminal_checks() -> None:
    inputs = _inputs()

    plan = build_plan(inputs, SystemState.empty())

    assert [action.name for action in plan.actions if isinstance(action, EnsureAccount)] == [
        "member-agent",
        "member-peer",
        "member-ops",
    ]
    group = next(action for action in plan.actions if isinstance(action, EnsureGroup))
    assert group.name == "member-services"
    assert group.members == ("member-agent", "member-peer")
    assert any(
        isinstance(action, EnsureDirectory)
        and action.spec.path == inputs.config.private_root
        and action.spec.mode == 0o700
        and action.spec.owner == "member-ops"
        for action in plan.actions
    )
    assert any(isinstance(action, EnsureFile) for action in plan.actions)
    assert any(isinstance(action, GenerateDeployKey) for action in plan.actions)
    assert any(isinstance(action, EnsureRepository) for action in plan.actions)
    assert any(isinstance(action, EnableTimer) for action in plan.actions)
    assert [action.name for action in plan.actions if isinstance(action, Check)] == [
        "hermes-gateway",
        "discord-readiness",
        "deploy-key-registration",
        "update-trust",
        "healthcheck",
    ]


def test_converged_system_only_plans_checks() -> None:
    inputs = _inputs()
    initial = build_plan(inputs, SystemState.empty())
    state = SystemState.from_actions(initial.actions)

    plan = build_plan(inputs, state)

    assert plan.actions == (
        Check("hermes-gateway"),
        Check("discord-readiness"),
        Check("deploy-key-registration"),
        Check("update-trust"),
        Check("healthcheck"),
    )


def test_command_sync_dropin_idempotent_after_first_plan() -> None:
    config = load_node_config(_REPO / "configs" / "node.example.toml")
    inputs = build_inputs(_REPO, config, _public_key())
    paths = {
        config.agent_home
        / ".config/systemd/user"
        / f"{config.agent_gateway_unit}.d"
        / "30-command-sync.conf",
        config.peer_home
        / ".config/systemd/user"
        / f"{config.peer_gateway_unit}.d"
        / "30-command-sync.conf",
    }

    initial = build_plan(inputs, SystemState.empty())
    planned_paths = {
        action.spec.path for action in initial.actions if isinstance(action, EnsureFile)
    }

    assert paths <= planned_paths

    converged = build_plan(inputs, SystemState.from_actions(initial.actions))
    repeated_paths = {
        action.spec.path for action in converged.actions if isinstance(action, EnsureFile)
    }
    assert paths.isdisjoint(repeated_paths)


def test_drifted_file_and_directory_are_converged_without_recreating_accounts() -> None:
    inputs = _inputs()
    desired = build_plan(inputs, SystemState.empty())
    state = SystemState.from_actions(desired.actions)
    service = inputs.files[0]
    drifted = replace(
        state,
        directories={
            **state.directories,
            inputs.config.private_root: DirectoryState(0o755, "root", "root"),
        },
        files={
            **state.files,
            service.path: FileState(_digest("old\n"), 0o600, "root", "root"),
        },
    )

    plan = build_plan(inputs, drifted)

    assert not any(isinstance(action, EnsureAccount) for action in plan.actions)
    assert any(
        isinstance(action, EnsureDirectory) and action.spec.path == inputs.config.private_root
        for action in plan.actions
    )
    assert [action.spec for action in plan.actions if isinstance(action, EnsureFile)] == [service]


def test_existing_private_key_is_never_regenerated() -> None:
    inputs = _inputs()
    key_path = inputs.config.ops_home / ".ssh" / "id_ed25519"

    plan = build_plan(inputs, replace(SystemState.empty(), private_keys=frozenset({key_path})))

    assert not any(isinstance(action, GenerateDeployKey) for action in plan.actions)


def test_partial_failure_replans_only_missing_tail() -> None:
    inputs = _inputs()
    initial = build_plan(inputs, SystemState.empty())
    prefix = initial.actions[:8]
    state = SystemState.from_actions(prefix)

    resumed = build_plan(inputs, state)

    assert not any(action in prefix for action in resumed.actions)
    assert any(isinstance(action, EnsureRepository) for action in resumed.actions)
    assert resumed.actions[-1] == Check("healthcheck")
