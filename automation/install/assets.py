from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Final, assert_never

from automation.install.components import EnableUserUnit, EnsureSymlink, resolve_components
from automation.install.component_assets import build_component_assets
from automation.install.libexec_assets import libexec_files
from automation.install.profiles import (
    HEALTHCHECK_DECLARATION_PATH,
    healthcheck_env,
    resolve_profile,
)
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
    ProvisionHealthcheckProbe,
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
    "autophagy-healthcheck-peer-read",
    "autophagy-orchestration",
    "autophagy-release-store",
    "autophagy-skill-store",
    "autophagy-supply-chain-resume",
)


#: The reconcile unit loads this with no `-` prefix, so an absent file is a hard
#: EnableTimer failure on a new node — which is exactly what a 2026-09-07 installation
#: hit. An EMPTY one is fine: owner_notice.py answers NOTIFY-UNCONFIGURED and convergence
#: still runs, so the node loses notices, not deployment.
OWNER_NOTICE_CREDENTIAL: Final = Path("/etc/autophagy/repair-approval.env")
OWNER_NOTICE_CREDENTIAL_TEMPLATE: Final = """\
# Owner-notice credentials, read by automation/owner_notice.py.
# Leaving this empty is valid: notices are skipped and convergence is unaffected.
# Fill these in to receive reconcile-failure and deploy-drift notices.
# DISCORD_BOT_TOKEN=
# AUTOPHAGY_OWNER_ID=
# OWNER_NOTICE_CHANNEL_ID=
"""


class InstallAssetError(RuntimeError):
    pass


def describe_action(action: InstallAction) -> str:
    match action:
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
        case ProvisionHealthcheckProbe(operator_home=home, private_path=key):
            return f"healthcheck-probe 키={key} 래퍼={home}/.local/libexec/autophagy-healthcheck-probe"
        case InstallGitleaks(version=version):
            return f"gitleaks version={version}"
        case EnsureRepository(path=path, origin_url=origin):
            return f"repository {path} origin={origin}"
        case EnableTimer(name=name):
            return f"timer {name} enabled"
        case EnsureSymlink(path=path, target=target, owner=owner):
            return f"symlink {path} 대상={target} 소유자={owner}"
        case EnableUserUnit(name=name, owner=owner):
            return f"user-unit {name} 사용자={owner} 활성화"
        case Check(name=name):
            return f"check {name}"
        case _:
            assert_never(action)


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
    profile: str | None = None,
) -> InstallInputs:
    config = replace(config, peer_attest_mode="signed")
    _validate_release_layout(config)
    chosen_profile = None if profile is None else resolve_profile(profile)
    names = (*components, *(chosen_profile.components if chosen_profile is not None else ()))
    selected = build_component_assets(repo_root, config, resolve_components(names))
    declaration = None if chosen_profile is None else healthcheck_env(chosen_profile)
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

    files.extend(selected.files)

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

    files.extend(libexec_files(repo_root, config))
    if declaration is not None:
        files.append(_file(HEALTHCHECK_DECLARATION_PATH, declaration, 0o644, root, root))

    files.append(
        replace(
            _file(
                OWNER_NOTICE_CREDENTIAL,
                OWNER_NOTICE_CREDENTIAL_TEMPLATE,
                0o640,
                root,
                config.ops_account,
            ),
            create_only=True,
        )
    )

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
    return InstallInputs(config, tuple(files), ENABLED_TIMERS + selected.timers, components=selected)
