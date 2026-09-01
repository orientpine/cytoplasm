from __future__ import annotations

import base64
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from automation.install.apply import SystemMutator
from automation.install.assets import build_inputs
from automation.install.plan import (
    EnsurePeerAttestKey,
    InstallInputs,
    SystemState,
    build_plan,
)
from automation.node_config import default_node_config


def _update_key() -> str:
    algorithm = b"ssh-ed25519"
    blob = len(algorithm).to_bytes(4, "big") + algorithm
    blob += (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} installer-test"


def test_installer_keeps_signed_seed_for_new_nodes() -> None:
    seed = default_node_config()

    inputs = build_inputs(Path.cwd(), seed, _update_key())

    assert seed.peer_attest_mode == "signed"
    assert inputs.config.peer_attest_mode == "signed"
    runtime_configs = {
        spec.path: spec.content
        for spec in inputs.files
        if spec.path.name == "node.toml"
    }
    assert set(runtime_configs) == {
        Path("/etc/autophagy/node.toml"),
        seed.agent_home / ".hermes" / "node.toml",
        seed.peer_home / ".hermes" / "node.toml",
        seed.ops_home / ".hermes" / "node.toml",
    }
    assert all('peer_attest_mode = "signed"\n' in content for content in runtime_configs.values())


def test_signed_install_plan_generates_peer_key_and_publishes_root_owned_copy() -> None:
    config = replace(default_node_config(), peer_attest_mode="signed")
    inputs = InstallInputs(config, (), ())

    plan = build_plan(inputs, SystemState.empty())

    action = next(action for action in plan.actions if isinstance(action, EnsurePeerAttestKey))
    assert action.private_path == config.peer_home / ".ssh" / "peer_attest_ed25519"
    assert action.public_path == Path(f"/etc/autophagy/peer-attest-{config.peer_account}.pub")
    assert action.owner == config.peer_account
    assert action.comment == f"{config.peer_account}@{config.primary_node_name}-peer-attest"


def test_converged_peer_attest_key_is_not_regenerated() -> None:
    config = replace(default_node_config(), peer_attest_mode="signed")
    inputs = InstallInputs(config, (), ())
    initial = build_plan(inputs, SystemState.empty())
    converged = SystemState.from_actions(initial.actions)

    resumed = build_plan(inputs, converged)

    assert not any(isinstance(action, EnsurePeerAttestKey) for action in resumed.actions)


def test_discord_install_plan_does_not_require_peer_signing_key() -> None:
    config = replace(default_node_config(), peer_attest_mode="discord")
    inputs = InstallInputs(config, (), ())

    plan = build_plan(inputs, SystemState.empty())

    assert not any(isinstance(action, EnsurePeerAttestKey) for action in plan.actions)


def test_peer_key_action_runs_as_peer_and_publishes_via_managed_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(
        default_node_config(),
        peer_account="peer-test",
        peer_home=tmp_path / "peer-home",
    )
    private_path = config.peer_home / ".ssh" / "peer_attest_ed25519"
    public_path = tmp_path / "etc" / "peer-attest-peer-test.pub"
    action = EnsurePeerAttestKey(
        private_path,
        public_path,
        config.peer_account,
        "peer-test@node-peer-attest",
    )
    mutator = SystemMutator(config)
    commands: list[tuple[str, ...]] = []
    writes: list[tuple[Path, str, int, str, str]] = []

    def fake_run(
        command: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del env, cwd
        commands.append(command)
        private_path.write_text("private\n", encoding="utf-8")
        private_path.with_suffix(".pub").write_text("ssh-ed25519 AAAATEST peer\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(mutator, "run", fake_run)
    monkeypatch.setattr(
        "automation.install.peer_attest_key.shutil.chown",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        mutator,
        "_write_file",
        lambda path, content, mode, owner, group: writes.append(
            (path, content, mode, owner, group)
        ),
    )

    mutator.apply(action)

    assert commands == [
        (
            "runuser",
            "-u",
            "peer-test",
            "--",
            "ssh-keygen",
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(private_path),
            "-C",
            "peer-test@node-peer-attest",
        )
    ]
    assert writes == [
        (public_path, "ssh-ed25519 AAAATEST peer\n", 0o644, "root", "root")
    ]
