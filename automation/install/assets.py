from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Final

from automation.install.components import resolve_components
from automation.install.plan import (
    Check,
    EnableTimer,
    EnsureAccount,
    EnsureDirectory,
    EnsureFile,
    EnsureGroup,
    EnsurePeerAttestKey,
    EnsureRepository,
    FileSpec,
    GenerateDeployKey,
    InstallAction,
    InstallGitleaks,
    InstallInputs,
    InstallPlan,
)
from automation.install.trust_key_bootstrap import plan_install
from automation.node_asset_renderer import render_asset
from automation.node_config import NodeConfig, node_config_values


SYSTEM_UNITS: Final = (
    "autophagy-deploy-reconcile.service",
    "autophagy-deploy-reconcile.timer",
    "autophagy-deploy-smoke.service",
    "autophagy-deploy-smoke.timer",
    "autophagy-supply-chain-watch.service",
    "autophagy-supply-chain-watch.timer",
)
ENABLED_TIMERS: Final = tuple(name for name in SYSTEM_UNITS if name.endswith(".timer"))
SUDOERS_ASSETS: Final = (
    "autophagy-deploy-reconcile",
    "autophagy-orchestration",
    "autophagy-release-store",
    "autophagy-skill-store",
    "autophagy-supply-chain-resume",
)


class InstallAssetError(RuntimeError):
    pass


def describe_action(action: InstallAction) -> str:
    match action:  # noqa: MATCH_OK - InstallAction is exhaustively consumed.
        case EnsureAccount(name=name, home=home):
            return f"account {name} home={home}"
        case EnsureGroup(name=name, members=members):
            return f"group {name} members={','.join(members)}"
        case EnsureDirectory(spec=spec):
            return f"directory {spec.path} owner={spec.owner}:{spec.group} mode={spec.mode:04o}"
        case EnsureFile(spec=spec):
            return (
                f"file {spec.path} owner={spec.owner}:{spec.group} mode={spec.mode:04o} "
                f"sha256={spec.state().digest}"
            )
        case GenerateDeployKey(private_path=path, comment=comment):
            return f"deploy-key {path} comment={comment} private=never-printed"
        case EnsurePeerAttestKey(
            private_path=private_path,
            public_path=public_path,
            owner=owner,
            comment=comment,
        ):
            return (
                f"peer-attest-key private={private_path} public={public_path} "
                f"owner={owner} comment={comment} private-content=never-printed"
            )
        case InstallGitleaks(version=version):
            return f"gitleaks version={version}"
        case EnsureRepository(path=path, origin_url=origin):
            return f"repository {path} origin={origin}"
        case EnableTimer(name=name):
            return f"timer {name} enabled"
        case Check(name=name):
            return f"check {name}"


def render_plan(plan: InstallPlan) -> str:
    lines = [f"{index:02d}. {describe_action(action)}" for index, action in enumerate(plan.actions, 1)]
    return "\n".join(lines)


def render_node_toml(config: NodeConfig) -> str:
    lines: list[str] = []
    for name, value in node_config_values(config).items():
        if name == "require_signed_updates":
            encoded = "true" if config.require_signed_updates else "false"
        else:
            encoded = json.dumps(value, ensure_ascii=False)
        lines.append(f"{name} = {encoded}\n")
    return "".join(lines)


def _file(path: Path, content: str, mode: int, owner: str, group: str) -> FileSpec:
    return FileSpec(path, content, mode, owner, group)


def _rendered(source: Path, config: NodeConfig) -> str:
    return render_asset(source, config)


def _validate_release_layout(config: NodeConfig) -> None:
    expected_store = config.service_root / "autophagy-agent-releases"
    expected_current = config.service_root / "autophagy-agent-current"
    if config.release_store != expected_store or config.release_current != expected_current:
        raise InstallAssetError(
            "release_store/release_current must retain the canonical basenames under service_root"
        )


def build_inputs(
    repo_root: Path,
    config: NodeConfig,
    update_trust_key: str,
    *,
    components: Sequence[str] = (),
) -> InstallInputs:
    config = replace(config, peer_attest_mode="signed")
    _validate_release_layout(config)
    selected = resolve_components(components)
    automation = repo_root / "automation"
    root = "root"
    ops = config.ops_account
    agent = config.agent_account
    files: list[FileSpec] = []

    trust = plan_install(update_trust_key)
    files.append(_file(trust.path, trust.content, trust.mode, root, root))

    node_toml = render_node_toml(config)
    files.append(_file(Path("/etc/autophagy/node.toml"), node_toml, 0o644, root, root))
    for account, home in (
        (agent, config.agent_home),
        (config.peer_account, config.peer_home),
        (ops, config.ops_home),
    ):
        files.append(_file(home / ".hermes" / "node.toml", node_toml, 0o600, account, account))

    command_sync_dropin = "[Service]\nEnvironment=DISCORD_COMMAND_SYNC_POLICY=bulk\n"
    for account, home, gateway_unit in (
        (agent, config.agent_home, config.agent_gateway_unit),
        (config.peer_account, config.peer_home, config.peer_gateway_unit),
    ):
        files.append(
            _file(
                home / ".config/systemd/user" / f"{gateway_unit}.d" / "30-command-sync.conf",
                command_sync_dropin,
                0o600,
                account,
                account,
            )
        )

    systemd_source = automation / "systemd"
    for name in SYSTEM_UNITS:
        files.append(
            _file(
                Path("/etc/systemd/system") / name,
                _rendered(systemd_source / name, config),
                0o644,
                root,
                root,
            )
        )

    # Opt-in components are appended in the same shape as the always-on units, so the
    # existing digest/enabled-timer comparison in build_plan gives them idempotency for
    # free: a second run re-derives identical FileSpecs and skips the already-enabled timer.
    for component in selected:
        component_source = repo_root / component.source
        for name in component.units:
            files.append(
                _file(
                    Path("/etc/systemd/system") / name,
                    _rendered(component_source / name, config),
                    0o644,
                    root,
                    root,
                )
            )

    sudoers_source = automation / "sudoers.d"
    for name in SUDOERS_ASSETS:
        files.append(
            _file(
                Path("/etc/sudoers.d") / name,
                _rendered(sudoers_source / name, config),
                0o440,
                root,
                root,
            )
        )

    helper_sources = (
        (automation / "release_store.py", config.libexec_dir / "autophagy-install-release", 0o755),
        (automation / "release_provenance.py", config.libexec_dir / "release_provenance.py", 0o644),
        (automation / "skill_store.py", config.libexec_dir / "autophagy-install-skill", 0o755),
        (
            automation / "converge_origin_main.sh",
            config.libexec_dir / "autophagy-converge-origin-main",
            0o755,
        ),
        (
            automation / "libexec" / "autophagy-resume-deploy",
            config.libexec_dir / "autophagy-resume-deploy",
            0o755,
        ),
        (
            automation / "origin_snapshot.sh",
            config.libexec_dir / "autophagy-converge.d" / "origin_snapshot.sh",
            0o755,
        ),
        (
            automation / "release_store.py",
            config.libexec_dir / "autophagy-converge.d" / "release_store.py",
            0o755,
        ),
        (
            automation / "release_provenance.py",
            config.libexec_dir / "autophagy-converge.d" / "release_provenance.py",
            0o644,
        ),
    )
    for source, destination, mode in helper_sources:
        files.append(_file(destination, _rendered(source, config), mode, root, root))

    hook_source = automation / "hooks"
    files.extend(
        (
            _file(
                config.deploy_checkout / ".git" / "hooks" / "pre-commit",
                (hook_source / "deploy-checkout-pre-commit").read_text(encoding="utf-8"),
                0o755,
                ops,
                config.service_group,
            ),
            _file(
                config.repair_work / ".git" / "hooks" / "pre-commit",
                (hook_source / "gitleaks-pre-commit").read_text(encoding="utf-8"),
                0o755,
                ops,
                ops,
            ),
        )
    )
    timers = ENABLED_TIMERS + tuple(
        name for component in selected for name in component.timers
    )
    return InstallInputs(config, tuple(files), timers)
