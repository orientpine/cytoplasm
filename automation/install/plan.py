"""Pure desired-state planner for one-node installations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeAlias, assert_never

from automation.install.components import EnableUserUnit as EnableUserUnit, EnsureSymlink as EnsureSymlink
from automation.install.component_assets import (
    ComponentAssets,
    DirectorySpec as DirectorySpec, DirectoryState as DirectoryState,
    FileSpec as FileSpec, FileState as FileState,
)
from automation.node_config import NodeConfig
from automation.update_trust_state import release_floor_path


CheckName: TypeAlias = Literal["hermes-gateway", "discord-readiness", "deploy-key-registration",
    "update-trust", "group-skill-trust", "healthcheck",
]


@dataclass(frozen=True, slots=True)
class EnsureAccount:
    name: str
    home: Path


@dataclass(frozen=True, slots=True)
class EnsureGroup:
    name: str
    members: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EnsureDirectory:
    spec: DirectorySpec


@dataclass(frozen=True, slots=True)
class EnsureFile:
    spec: FileSpec


@dataclass(frozen=True, slots=True)
class GenerateDeployKey:
    private_path: Path
    comment: str


@dataclass(frozen=True, slots=True)
class EnsurePeerAttestKey:
    private_path: Path
    public_path: Path
    owner: str
    comment: str


@dataclass(frozen=True, slots=True)
class ProvisionHealthcheckProbe:
    operator_account: str
    operator_home: Path
    ops_account: str
    ops_home: Path
    private_path: Path
    source_dir: Path
    node_name: str


@dataclass(frozen=True, slots=True)
class InstallGitleaks:
    version: str


@dataclass(frozen=True, slots=True)
class EnsureRepository:
    path: Path
    origin_url: str
    private_key: Path


@dataclass(frozen=True, slots=True)
class EnableTimer:
    name: str


@dataclass(frozen=True, slots=True)
class Check:
    name: CheckName


InstallAction: TypeAlias = (
    EnsureAccount | EnsureGroup | EnsureDirectory | EnsureFile | GenerateDeployKey | EnsurePeerAttestKey
    | ProvisionHealthcheckProbe | InstallGitleaks | EnsureRepository | EnableTimer | Check
    | EnableUserUnit | EnsureSymlink
)


@dataclass(frozen=True, slots=True)
class InstallInputs:
    config: NodeConfig
    files: tuple[FileSpec, ...]
    timers: tuple[str, ...]
    gitleaks_version: str = "8.30.1"
    trust_checks: tuple[CheckName, ...] = ("update-trust",)
    components: ComponentAssets = ComponentAssets()


@dataclass(frozen=True, slots=True)
class SystemState:
    accounts: frozenset[str] = frozenset()
    ready_accounts: frozenset[str] = frozenset()
    groups: Mapping[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])
    directories: Mapping[Path, DirectoryState] = field(default_factory=dict[Path, DirectoryState])
    files: Mapping[Path, FileState] = field(default_factory=dict[Path, FileState])
    private_keys: frozenset[Path] = frozenset()
    peer_attest_keys: frozenset[Path] = frozenset()
    repositories: Mapping[Path, str] = field(default_factory=dict[Path, str])
    operator_home: Path | None = None
    healthcheck_probe_ready: bool = False
    enabled_timers: frozenset[str] = frozenset()
    enabled_user_units: frozenset[EnableUserUnit] = frozenset()
    symlinks: frozenset[EnsureSymlink] = frozenset()
    gitleaks_version: str | None = None

    @classmethod
    def empty(cls) -> SystemState:
        return cls()

    @classmethod
    def from_actions(cls, actions: Sequence[InstallAction]) -> SystemState:
        accounts: set[str] = set()
        ready_accounts: set[str] = set()
        groups: dict[str, frozenset[str]] = {}
        directories: dict[Path, DirectoryState] = {}
        files: dict[Path, FileState] = {}
        private_keys: set[Path] = set()
        peer_attest_keys: set[Path] = set()
        repositories: dict[Path, str] = {}
        enabled_timers: set[str] = set()
        enabled_user_units: set[EnableUserUnit] = set()
        symlinks: set[EnsureSymlink] = set()
        gitleaks_version: str | None = None
        healthcheck_probe_ready = False
        for action in actions:
            match action:
                case EnsureAccount(name=name):
                    accounts.add(name)
                    ready_accounts.add(name)
                case EnsureGroup(name=name, members=members):
                    groups[name] = frozenset(members)
                case EnsureDirectory(spec=spec):
                    directories[spec.path] = spec.state()
                case EnsureFile(spec=spec):
                    files[spec.path] = spec.state()
                case GenerateDeployKey(private_path=private_path):
                    private_keys.add(private_path)
                case EnsurePeerAttestKey(private_path=private_path):
                    peer_attest_keys.add(private_path)
                case ProvisionHealthcheckProbe():
                    healthcheck_probe_ready = True
                case InstallGitleaks(version=version):
                    gitleaks_version = version
                case EnsureRepository(path=path, origin_url=origin_url):
                    repositories[path] = origin_url
                case EnableTimer(name=name):
                    enabled_timers.add(name)
                case EnableUserUnit():
                    enabled_user_units.add(action)
                case EnsureSymlink():
                    symlinks.add(action)
                case Check():
                    continue
                case _:
                    assert_never(action)
        return cls(
            accounts=frozenset(accounts),
            ready_accounts=frozenset(ready_accounts),
            groups=groups,
            directories=directories,
            files=files,
            private_keys=frozenset(private_keys),
            peer_attest_keys=frozenset(peer_attest_keys),
            repositories=repositories,
            enabled_timers=frozenset(enabled_timers),
            enabled_user_units=frozenset(enabled_user_units),
            symlinks=frozenset(symlinks),
            gitleaks_version=gitleaks_version,
            healthcheck_probe_ready=healthcheck_probe_ready,
        )


@dataclass(frozen=True, slots=True)
class InstallPlan:
    actions: tuple[InstallAction, ...]


def _directories(config: NodeConfig) -> tuple[DirectorySpec, ...]:
    root = "root"
    ops = config.ops_account
    agent = config.agent_account
    group = config.service_group
    return (
        DirectorySpec(config.private_root, 0o700, ops, ops),
        DirectorySpec(config.private_root / "runtime-logs", 0o700, ops, ops),
        DirectorySpec(config.private_root / "repair-logs", 0o700, ops, ops),
        DirectorySpec(config.private_root / "deploy-reconcile", 0o700, ops, ops),
        DirectorySpec(config.private_root / "locks", 0o2770, ops, group),
        DirectorySpec(config.deploy_checkout, 0o2750, ops, group),
        DirectorySpec(config.release_store, 0o755, root, root),
        DirectorySpec(config.skill_store, 0o755, root, root),
        DirectorySpec(config.skill_store / "releases", 0o755, root, root),
        DirectorySpec(config.skill_store / "live", 0o755, root, root),
        DirectorySpec(config.repair_work, 0o700, ops, ops),
        DirectorySpec(config.repair_report_queue, 0o2750, ops, group),
        DirectorySpec(config.repair_report_ack, 0o2750, agent, ops),
        DirectorySpec(config.repair_capability, 0o2750, agent, ops),
        DirectorySpec(config.libexec_dir, 0o755, root, root),
        DirectorySpec(config.libexec_dir / "autophagy-converge.d", 0o755, root, root),
        # root writes the rollback anchor, ops reads it in the converge pre-gate. The
        # provisioner has always created it 0755; the installer did not, so the first
        # write made it 0700 and every later tick skipped (2026-09-08).
        DirectorySpec(release_floor_path(config).parent, 0o755, root, root),
        DirectorySpec(config.agent_home / ".hermes", 0o700, agent, agent),
        DirectorySpec(config.peer_home / ".hermes", 0o700, config.peer_account, config.peer_account),
        DirectorySpec(config.peer_home / ".ssh", 0o700, config.peer_account, config.peer_account),
        DirectorySpec(config.ops_home / ".hermes", 0o700, ops, ops),
        DirectorySpec(config.ops_home / ".ssh", 0o700, ops, ops),
    )


def build_plan(inputs: InstallInputs, state: SystemState) -> InstallPlan:
    config = inputs.config
    actions: list[InstallAction] = []
    for name, home in (
        (config.agent_account, config.agent_home),
        (config.peer_account, config.peer_home),
        (config.ops_account, config.ops_home),
    ):
        if name not in state.ready_accounts:
            actions.append(EnsureAccount(name, home))
    members = (config.agent_account, config.peer_account)
    current_members = state.groups.get(config.service_group)
    if current_members is None or not frozenset(members).issubset(current_members):
        actions.append(EnsureGroup(config.service_group, members))
    for spec in (*_directories(config), *inputs.components.directories):
        if state.directories.get(spec.path) != spec.state():
            actions.append(EnsureDirectory(spec))
    if config.peer_attest_mode == "signed":
        peer_private_key = config.peer_home / ".ssh" / "peer_attest_ed25519"
        if peer_private_key not in state.peer_attest_keys:
            actions.append(
                EnsurePeerAttestKey(
                    peer_private_key, Path(f"/etc/autophagy/peer-attest-{config.peer_account}.pub"), config.peer_account,
                    f"{config.peer_account}@{config.primary_node_name}-peer-attest",
                )
            )
    actions.extend((Check("hermes-gateway"), Check("discord-readiness")))
    private_key = config.ops_home / ".ssh" / "id_ed25519"
    if private_key not in state.private_keys:
        comment = f"{config.ops_account}@{config.primary_node_name}-autophagy-deploy"
        actions.append(GenerateDeployKey(private_key, comment))
    actions.append(Check("deploy-key-registration"))
    if state.gitleaks_version != inputs.gitleaks_version:
        actions.append(InstallGitleaks(inputs.gitleaks_version))
    for path in (config.deploy_checkout, config.repair_work):
        if state.repositories.get(path) != config.origin_url:
            actions.append(EnsureRepository(path, config.origin_url, private_key))
    for spec in inputs.files:
        if spec.create_only and spec.path in state.files:
            continue
        if state.files.get(spec.path) != spec.state():
            actions.append(EnsureFile(spec))
    actions.extend(link for link in inputs.components.symlinks if link not in state.symlinks)
    if not state.healthcheck_probe_ready:
        home = state.operator_home or (Path('/root') if config.operator_account == 'root' else Path('/home') / config.operator_account)
        actions.append(ProvisionHealthcheckProbe(config.operator_account, home, config.ops_account, config.ops_home,
                       config.ops_home / '.ssh/autophagy-healthcheck', config.deploy_checkout, config.primary_node_name))
    for timer in inputs.timers:
        if timer not in state.enabled_timers:
            actions.append(EnableTimer(timer))
    actions.extend(unit for unit in inputs.components.user_units if unit not in state.enabled_user_units)
    actions.extend(Check(name) for name in inputs.trust_checks)
    actions.append(Check("healthcheck"))
    return InstallPlan(tuple(actions))
