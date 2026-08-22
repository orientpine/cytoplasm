from __future__ import annotations

import subprocess
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from automation.install.checks import CheckResult, Status
from automation.install.discord_check import main as discord_check_main
from automation.install.plan import (
    Check,
    CheckName,
    InstallAction,
)
from automation.install.trust_key_bootstrap import (
    MANAGED_SKILLS_ALLOWED_SIGNERS_PATH,
    RealFilesystem,
    TrustKeyError,
    fingerprint,
    parse_public_key,
    verify_group_installed,
    verify_installed,
)
from automation.node_config import NodeConfig
class _Mutator(Protocol):
    def apply(self, action: InstallAction) -> None: ...

    def run(
        self,
        command: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


class _MutatorFactory(Protocol):
    def __call__(self, config: NodeConfig) -> _Mutator: ...


def _load_mutator(config: NodeConfig) -> _Mutator:
    factory = cast(
        _MutatorFactory,
        getattr(import_module("automation.install.apply"), "SystemMutator"),
    )
    return factory(config)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    config: NodeConfig
    repo_root: Path
    discord_config: Path
    expected_trust_fingerprint: str | None
    expected_group_skill_fingerprint: str | None = None


class RealExecutor:
    _context: ExecutionContext
    _mutator: _Mutator

    def __init__(self, context: ExecutionContext) -> None:
        self._context = context
        self._mutator = _load_mutator(context.config)

    def execute(self, action: InstallAction) -> tuple[CheckResult, ...]:
        try:
            return self._execute(action)
        except (OSError, subprocess.CalledProcessError, TrustKeyError) as error:
            # OSError and TrustKeyError carry a named prerequisite here
            # (KNOWN-HOSTS-MISSING, an unsafe origin_url, a trust-file refusal);
            # dropping their text left the operator with a type name and
            # nothing to act on. CalledProcessError stays terse because its
            # str() is the whole argv.
            named = type(error).__name__ if isinstance(
                error, subprocess.CalledProcessError
            ) else str(error)
            detail = f"{type(action).__name__} failed: {named}"
            return (CheckResult(type(action).__name__, Status.FAIL, detail),)

    def _execute(self, action: InstallAction) -> tuple[CheckResult, ...]:
        if isinstance(action, Check):
            return self._check(action.name)
        self._mutator.apply(action)
        return (CheckResult(type(action).__name__, Status.PASS, "converged"),)

    def _run(
        self,
        command: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self._mutator.run(command, env=env, cwd=cwd)

    def _check(self, name: CheckName) -> tuple[CheckResult, ...]:
        match name:
            case "deploy-key-registration":
                public_path = self._context.config.ops_home / ".ssh" / "id_ed25519.pub"
                public_key = parse_public_key(
                    public_path.read_text(encoding="utf-8")
                )
                detail = "\n".join(
                    (
                        "GROUP-JOIN-DEPLOY-PUBLIC-KEY access=read-only",
                        "send this exact public-key line to your group admin:",
                        public_key.line(),
                        f"fingerprint={fingerprint(public_key)}",
                        "share/compare fingerprints out-of-band",
                        "GROUP-DISCORD-FORBIDDEN: never use the group Discord channel",
                        "the private key stays on this installation",
                    )
                )
                return (CheckResult(name, Status.PASS, detail),)
            case "hermes-gateway":
                return (self._gateway_check(),)
            case "discord-readiness":
                code = discord_check_main(("--config", str(self._context.discord_config)))
                status = Status.PASS if code == 0 else Status.FAIL
                return (CheckResult(name, status, f"discord_check.py rc={code}"),)
            case "update-trust":
                return verify_installed(
                    Path("/etc/autophagy/update-allowed-signers"),
                    RealFilesystem(),
                    expected_fingerprint=self._context.expected_trust_fingerprint,
                )
            case "group-skill-trust":
                return verify_group_installed(
                    MANAGED_SKILLS_ALLOWED_SIGNERS_PATH,
                    RealFilesystem(),
                    expected_fingerprint=self._context.expected_group_skill_fingerprint,
                )
            case "healthcheck":
                return (self._healthcheck(),)

    def _gateway_check(self) -> CheckResult:
        config = self._context.config
        for account, home, unit in (
            (config.agent_account, config.agent_home, config.agent_gateway_unit),
            (config.peer_account, config.peer_home, config.peer_gateway_unit),
        ):
            uid = self._run(("id", "-u", account)).stdout.strip()
            environment = (
                "PATH=" + str(home / ".local" / "bin") + ":/usr/local/bin:/usr/bin:/bin"
            )
            try:
                _ = self._run(
                    (
                        "runuser",
                        "-u",
                        account,
                        "--",
                        "env",
                        f"HOME={home}",
                        environment,
                        "hermes",
                        "--version",
                    )
                )
                _ = self._run(
                    (
                        "runuser",
                        "-u",
                        account,
                        "--",
                        "env",
                        f"XDG_RUNTIME_DIR=/run/user/{uid}",
                        "systemctl",
                        "--user",
                        "is-active",
                        "--quiet",
                        unit,
                    )
                )
            except subprocess.CalledProcessError:
                detail = (
                    f"Hermes is an external prerequisite for {account}; install it in {home}, "
                    f"install/start {unit}, then rerun. The installer never installs Hermes."
                )
                return CheckResult("hermes-gateway", Status.FAIL, detail)
        return CheckResult("hermes-gateway", Status.PASS, "agent and peer Hermes gateways are active")

    def _healthcheck(self) -> CheckResult:
        config = self._context.config
        script = config.deploy_checkout / "automation" / "healthcheck.sh"
        try:
            _ = self._run(
                (
                    "runuser",
                    "-u",
                    config.ops_account,
                    "--",
                    "env",
                    f"HOME={config.ops_home}",
                    "bash",
                    str(script),
                )
            )
        except subprocess.CalledProcessError as error:
            return CheckResult("healthcheck", Status.FAIL, f"healthcheck.sh rc={error.returncode}")
        return CheckResult("healthcheck", Status.PASS, "healthcheck.sh ALL_HEALTHY")
