from __future__ import annotations

import base64
import os
import pwd
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest

from automation.install import plan as planning
from automation.install.assets import describe_action
from automation.install.healthcheck_probe_asset import (
    append_binding, generator_command, inspect_probe, provision_probe, wrapper_path,
)
from automation.install.installer import main
from automation.node_config import NodeConfig, default_node_config

_REPO: Final = Path(__file__).resolve().parents[2]


def _public_key() -> str:
    algorithm = b'ssh-ed25519'
    blob = len(algorithm).to_bytes(4, 'big') + algorithm + (32).to_bytes(4, 'big') + bytes(range(32))
    return f'ssh-ed25519 {base64.b64encode(blob).decode()} test'


def _probe_action(config: NodeConfig, home: Path | None = None) -> planning.ProvisionHealthcheckProbe:
    state = replace(planning.SystemState.empty(), operator_home=home)
    plan = planning.build_plan(planning.InstallInputs(config, (), ()), state)
    return next(action for action in plan.actions if isinstance(action, planning.ProvisionHealthcheckProbe))


def test_probe_is_planned_after_files_when_host_is_empty() -> None:
    # Given
    config = default_node_config()
    spec = planning.FileSpec(Path('/etc/autophagy/node.toml'), '', 0o644, 'root', 'root')
    inputs = planning.InstallInputs(config, (spec,), ('autophagy-deploy-reconcile.timer',))
    # When
    actions = planning.build_plan(inputs, planning.SystemState.empty()).actions
    # Then
    probe = next(action for action in actions if isinstance(action, planning.ProvisionHealthcheckProbe))
    index = actions.index(probe)
    assert all(i < index for i, action in enumerate(actions) if isinstance(action, (planning.EnsureRepository, planning.EnsureFile)))
    assert all(i > index for i, action in enumerate(actions) if isinstance(action, planning.EnableTimer))
    assert probe.source_dir == config.deploy_checkout
    assert probe.private_path == config.ops_home / '.ssh/autophagy-healthcheck'


def test_probe_is_absent_when_actions_have_converged() -> None:
    # Given
    inputs = planning.InstallInputs(default_node_config(), (), ())
    initial = planning.build_plan(inputs, planning.SystemState.empty())
    # When
    repeated = planning.build_plan(inputs, planning.SystemState.from_actions(initial.actions))
    # Then
    assert all(isinstance(action, planning.Check) for action in repeated.actions)


@pytest.fixture
def installed_probe(tmp_path: Path) -> planning.ProvisionHealthcheckProbe:
    account = pwd.getpwuid(os.getuid()).pw_name
    config = replace(default_node_config(), operator_account=account, ops_account=account,
                     ops_home=tmp_path / 'ops', deploy_checkout=tmp_path / 'checkout')
    action = _probe_action(config, tmp_path / 'operator')
    action.source_dir.mkdir()
    wrapper = wrapper_path(action)
    wrapper.parent.mkdir(parents=True)
    _ = wrapper.write_text('# wrapper-inputs: ' + 'a' * 64 + '\n', encoding='utf-8')
    wrapper.chmod(0o755)
    action.private_path.parent.mkdir(parents=True, mode=0o700)
    action.private_path.touch(mode=0o600)
    _ = action.private_path.with_suffix('.pub').write_text(_public_key() + '\n', encoding='utf-8')
    action.private_path.with_suffix('.pub').chmod(0o644)
    authorized = action.operator_home / '.ssh/authorized_keys'
    authorized.parent.mkdir(mode=0o700)
    _ = authorized.write_text(append_binding('', _public_key(), wrapper), encoding='utf-8')
    authorized.chmod(0o600)
    return action


@pytest.mark.parametrize('expectation', [('a' * 64, True), ('b' * 64, False), ('', False)])
def test_probe_convergence_tracks_digest_when_assets_exist(
    installed_probe: planning.ProvisionHealthcheckProbe, monkeypatch: pytest.MonkeyPatch,
    expectation: tuple[str, bool],
) -> None:
    # Given
    digest, ready = expectation
    def generate(command: tuple[str, ...], *, cwd: Path, check: bool, capture_output: bool, text: bool) -> subprocess.CompletedProcess[str]:
        assert not check and capture_output and text
        assert command == generator_command(installed_probe, '--inputs-digest')
        assert cwd == installed_probe.source_dir
        return subprocess.CompletedProcess(command, 0, digest + '\n', '')
    monkeypatch.setattr('automation.install.healthcheck_probe_asset.subprocess.run', generate)
    # When
    actual = inspect_probe(installed_probe)
    # Then
    assert actual is ready
    config = default_node_config()
    state = replace(planning.SystemState.empty(), healthcheck_probe_ready=actual)
    planned = planning.build_plan(planning.InstallInputs(config, (), ()), state)
    assert any(isinstance(item, planning.ProvisionHealthcheckProbe) for item in planned.actions) is not ready


@pytest.mark.parametrize('foreign', ['# 다른 운영자 키\nssh-ed25519 AAAAforeign owner\n', '# 마지막 줄에 개행 없음'])
def test_binding_preserves_foreign_lines_when_appended_twice(tmp_path: Path, foreign: str) -> None:
    # Given
    authorized = tmp_path / 'authorized_keys'
    _ = authorized.write_text(foreign, encoding='utf-8')
    wrapper = tmp_path / '.local/libexec/autophagy-healthcheck-probe'
    public = _public_key()
    # When
    first = append_binding(authorized.read_text(encoding='utf-8'), public, wrapper)
    second = append_binding(first, public, wrapper)
    # Then
    assert second == first
    assert second.startswith(foreign)
    assert second.splitlines()[-1] == f'restrict,command="{wrapper}" {public}'
    assert sum(public in line for line in second.splitlines()) == 1


def test_existing_key_is_preserved_when_comment_differs() -> None:
    # Given
    existing = f'restrict,command="/wrapper" {_public_key()} old\n'
    # When
    result = append_binding(existing, _public_key() + ' new', Path('/wrapper'))
    # Then
    assert result == existing


def test_key_is_appended_when_material_is_only_a_prefix() -> None:
    # Given
    algorithm, material, _ = _public_key().split()
    existing = f'{algorithm} {material}AAAA foreign\n'
    # When
    result = append_binding(existing, _public_key(), Path('/wrapper'))
    # Then
    assert result.splitlines()[-1] == f'restrict,command="/wrapper" {_public_key()}'


def test_description_identifies_asset_when_probe_is_planned() -> None:
    # Given
    action = _probe_action(default_node_config())
    # When
    rendered = describe_action(action)
    # Then
    assert str(action.private_path) in rendered
    assert str(wrapper_path(action)) in rendered


def test_provision_fails_when_generator_omits_completion_marker(
    installed_probe: planning.ProvisionHealthcheckProbe,
) -> None:
    # Given
    def run(command: tuple[str, ...], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        assert env is None
        assert command == generator_command(installed_probe, '--install')
        assert cwd == installed_probe.source_dir
        return subprocess.CompletedProcess(command, 0, '', '')
    # When / Then
    with pytest.raises(OSError, match='HEALTHCHECK-WRAPPER-UNCONFIRMED'):
        provision_probe(installed_probe, run)


def test_dry_run_shows_probe_when_host_is_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Given
    trust = tmp_path / 'trust.pub'
    _ = trust.write_text(_public_key() + '\n', encoding='utf-8')
    # When
    result = main(('--config', str(_REPO / 'configs/node.example.toml'), '--update-trust-key', str(trust), '--dry-run'))
    # Then
    assert result == 0
    assert 'autophagy-healthcheck-probe' in capsys.readouterr().out
