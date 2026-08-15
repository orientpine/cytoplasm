from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import tempfile
from importlib import import_module
from pathlib import Path
from typing import Protocol

from automation.git_remote_url import GitRemoteUrlError, validate_remote_url
from automation.install.checks import CheckResult, Status
from automation.install.gitleaks import expected_archive_sha256, verify_archive
from automation.install.plan import (
    Check,
    EnableTimer,
    EnsureAccount,
    EnsureDirectory,
    EnsureFile,
    EnsureGroup,
    EnsurePeerAttestKey,
    EnsureRepository,
    GenerateDeployKey,
    InstallAction,
    InstallGitleaks,
    InstallPlan,
)
from automation.node_config import NodeConfig


class MutationDispatchError(RuntimeError):
    pass


class ActionExecutor(Protocol):
    def execute(self, action: InstallAction) -> tuple[CheckResult, ...]: ...


class SystemMutator:
    _config: NodeConfig

    def __init__(self, config: NodeConfig) -> None:
        self._config = config

    def run(
        self,
        command: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
        )

    def apply(self, action: InstallAction) -> None:
        match action:  # noqa: MATCH_OK - InstallAction is exhaustively consumed.
            case EnsureAccount():
                self._account(action)
            case EnsureGroup():
                self._group(action)
            case EnsureDirectory(spec=spec):
                spec.path.mkdir(parents=True, exist_ok=True)
                os.chmod(spec.path, spec.mode)
                shutil.chown(spec.path, user=spec.owner, group=spec.group)
            case EnsureFile(spec=spec):
                self._write_file(spec.path, spec.content, spec.mode, spec.owner, spec.group)
            case GenerateDeployKey():
                self._deploy_key(action)
            case EnsurePeerAttestKey():
                helper = getattr(
                    import_module("automation.install.peer_attest_key"),
                    "ensure_peer_attest_key",
                )
                helper(action, self.run, self._write_file)
            case InstallGitleaks(version=version):
                self._install_gitleaks(version)
            case EnsureRepository():
                self._repository(action)
            case EnableTimer(name=name):
                self._timer(name)
            case Check():
                raise MutationDispatchError

    def _account(self, action: EnsureAccount) -> None:
        result = subprocess.run(("id", "-u", action.name), check=False, capture_output=True)
        if result.returncode != 0:
            _ = self.run(
                (
                    "useradd",
                    "-m",
                    "-d",
                    str(action.home),
                    "-s",
                    "/bin/bash",
                    action.name,
                )
            )
        action.home.mkdir(parents=True, exist_ok=True)
        os.chmod(action.home, 0o700)
        shutil.chown(action.home, user=action.name, group=action.name)
        secrets = action.home / ".env.secrets"
        _ = secrets.touch(exist_ok=True)
        os.chmod(secrets, 0o600)
        shutil.chown(secrets, user=action.name, group=action.name)
        _ = self.run(("loginctl", "enable-linger", action.name))

    def _group(self, action: EnsureGroup) -> None:
        _ = self.run(("groupadd", "-f", action.name))
        for member in action.members:
            groups = self.run(("id", "-nG", member)).stdout.split()
            if action.name not in groups:
                _ = self.run(("usermod", "-aG", action.name, member))

    def _write_file(self, path: Path, content: str, mode: int, owner: str, group: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                _ = stream.write(content)
                _ = stream.flush()
                _ = os.fsync(stream.fileno())
            os.chmod(temporary_path, mode)
            shutil.chown(temporary_path, user=owner, group=group)
            if path.parent == Path("/etc/sudoers.d"):
                _ = self.run(("visudo", "-cf", str(temporary_path)))
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _deploy_key(self, action: GenerateDeployKey) -> None:
        action.private_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(action.private_path.parent, 0o700)
        shutil.chown(
            action.private_path.parent,
            user=self._config.ops_account,
            group=self._config.ops_account,
        )
        _ = self.run(
            (
                "runuser",
                "-u",
                self._config.ops_account,
                "--",
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                str(action.private_path),
                "-C",
                action.comment,
            )
        )

    def _install_gitleaks(self, version: str) -> None:
        architecture = platform.machine()
        archive_arch = {
            "aarch64": "arm64",
            "arm64": "arm64",
            "x86_64": "x64",
        }.get(architecture)
        if archive_arch is None:
            raise OSError(f"unsupported architecture: {architecture}")
        expected = expected_archive_sha256(version, archive_arch)
        url = (
            "https://github.com/gitleaks/gitleaks/releases/download/"
            f"v{version}/gitleaks_{version}_linux_{archive_arch}.tar.gz"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "gitleaks.tar.gz"
            _ = self.run(("curl", "-fsSL", "-o", str(archive), url))
            # Fail closed before anything from the archive is unpacked or run.
            _ = verify_archive(archive, expected)
            # --no-same-owner/--no-same-permissions: a tarball never gets to
            # choose ownership or setuid bits on a root extraction, even though
            # the digest check above should already have stopped a hostile one.
            _ = self.run(
                (
                    "tar",
                    "-xzf",
                    str(archive),
                    "-C",
                    str(root),
                    "--no-same-owner",
                    "--no-same-permissions",
                )
            )
            _ = self.run(
                (
                    "install",
                    "-m",
                    "0755",
                    str(root / "gitleaks"),
                    "/usr/local/bin/gitleaks",
                )
            )

    def _repository(self, action: EnsureRepository) -> None:
        try:
            origin_url = validate_remote_url(action.origin_url, label="origin_url")
        except GitRemoteUrlError as error:
            # The executor renders OSError as a FAIL result; a bare ValueError
            # would escape as a traceback instead.
            raise OSError(str(error)) from error
        ssh_command = shlex.join(
            (
                "ssh",
                "-i",
                str(action.private_key),
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
            )
        )
        environment = {
            **os.environ,
            "GIT_SSH_COMMAND": ssh_command,
            "HOME": str(self._config.ops_home),
        }
        _ = self.run(
            (
                "runuser",
                "-u",
                self._config.ops_account,
                "--",
                "env",
                f"HOME={self._config.ops_home}",
                f"GIT_SSH_COMMAND={ssh_command}",
                "git",
                "clone",
                # `--` keeps a dash-leading URL out of git's option namespace.
                "--",
                origin_url,
                str(action.path),
            ),
            env=environment,
        )

    def _timer(self, name: str) -> None:
        _ = self.run(("systemctl", "daemon-reload"))
        if name == "autophagy-deploy-reconcile.timer":
            _ = self.run(("systemctl", "start", "autophagy-deploy-reconcile.service"))
        _ = self.run(("systemctl", "enable", "--now", name))


def apply_plan(plan: InstallPlan, executor: ActionExecutor) -> tuple[CheckResult, ...]:
    results: list[CheckResult] = []
    for action in plan.actions:
        current = executor.execute(action)
        results.extend(current)
        if any(result.status is Status.FAIL for result in current):
            break
    return tuple(results)
