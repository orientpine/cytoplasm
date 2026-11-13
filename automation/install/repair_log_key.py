"""Provision the agent-to-ops forced-command transport used by repair_cli."""
from __future__ import annotations

import os
import pwd
import shlex
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from automation.install.allowed_signers import PublicKey, TrustKeyError, parse_public_key

if TYPE_CHECKING:
    from automation.install.peer_attest_key import CommandRunner


@dataclass(frozen=True, slots=True)
class EnsureRepairLogKey:
    agent_account: str
    agent_home: Path
    ops_account: str
    ops_home: Path
    receiver: Path
    host_public_path: Path = Path("/etc/ssh/ssh_host_ed25519_key.pub")

    @property
    def private_path(self) -> Path:
        return self.agent_home / ".ssh/autophagy-repair-log"

    @property
    def authorized_path(self) -> Path:
        return self.ops_home / ".ssh/authorized_keys"

    @property
    def known_hosts_path(self) -> Path:
        return self.agent_home / ".ssh/known_hosts"


def _public(path: Path) -> PublicKey:
    try:
        public = parse_public_key(path.read_text(encoding="utf-8"))
    except TrustKeyError as error:
        raise OSError("repair-log public key is invalid") from error
    if public.algorithm != "ssh-ed25519":
        raise OSError("repair-log requires an ed25519 public key")
    return public


def _binding(action: EnsureRepairLogKey) -> str:
    command = shlex.join(("/usr/bin/python3", "-I", str(action.receiver)))
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    return f'restrict,command="{escaped}" '


def _carries_key(line: str, public: PublicKey) -> bool:
    fields = line.split()
    return not line.lstrip().startswith("#") and any(
        algorithm == public.algorithm and material == public.material
        for algorithm, material in zip(fields, fields[1:])
    )


def _bound(existing: str, public: PublicKey, action: EnsureRepairLogKey) -> bool:
    matching = [line for line in existing.splitlines() if _carries_key(line, public)]
    return bool(matching) and all(line.startswith(_binding(action)) for line in matching)


def _host_known(existing: str, public: PublicKey) -> bool:
    for line in existing.splitlines():
        fields = line.split()
        if (len(fields) >= 3 and "127.0.0.1" in fields[0].split(",")
                and fields[1:3] == [public.algorithm, public.material]):
            return True
    return False


def _assets(action: EnsureRepairLogKey) -> tuple[tuple[Path, int, str], ...]:
    return (
        (action.private_path.parent, 0o700, action.agent_account),
        (action.authorized_path.parent, 0o700, action.ops_account),
        (action.private_path, 0o600, action.agent_account),
        (action.private_path.with_suffix(".pub"), 0o644, action.agent_account),
        (action.authorized_path, 0o600, action.ops_account),
        (action.known_hosts_path, 0o600, action.agent_account),
    )


def inspect_repair_log_key(action: EnsureRepairLogKey) -> bool:
    """Check public bindings and metadata; never read private key contents."""
    try:
        for path, mode, owner in _assets(action):
            metadata = path.lstat()
            account = pwd.getpwnam(owner)
            expected_type = stat.S_ISDIR if mode == 0o700 else stat.S_ISREG
            if (not expected_type(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != mode
                    or metadata.st_uid != account.pw_uid or metadata.st_gid != account.pw_gid):
                return False
        public = _public(action.private_path.with_suffix(".pub"))
        host = _public(action.host_public_path)
        return (
            _bound(action.authorized_path.read_text(encoding="utf-8"), public, action)
            and _host_known(action.known_hosts_path.read_text(encoding="utf-8"), host)
        )
    except (OSError, KeyError, UnicodeError):
        return False


def _append(path: Path, addition: str, owner: str) -> None:
    """Append without replacing unrelated entries, and converge SSH permissions."""
    if os.path.lexists(path) and not stat.S_ISREG(path.lstat().st_mode):
        raise OSError("repair-log SSH asset is not a regular file")
    with path.open("a+", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        shutil.chown(path, user=owner, group=owner)
        _ = stream.seek(0)
        existing = stream.read()
        if addition:
            separator = "" if not existing or existing.endswith("\n") else "\n"
            _ = stream.write(separator + addition + "\n")


def ensure_repair_log_key(action: EnsureRepairLogKey, run: CommandRunner) -> None:
    """Create a missing pair as agent, bind ops, and pin the local host public key."""
    public_path = action.private_path.with_suffix(".pub")
    if os.path.lexists(action.private_path) != os.path.lexists(public_path):
        raise OSError("repair-log keypair is partial; refusing regeneration")
    host = _public(action.host_public_path)
    for directory, owner in (
        (action.private_path.parent, action.agent_account),
        (action.authorized_path.parent, action.ops_account),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise OSError("repair-log SSH directory is not a directory")
        os.chmod(directory, 0o700)
        shutil.chown(directory, user=owner, group=owner)
    if not os.path.lexists(action.private_path):
        _ = run((
            "runuser", "-u", action.agent_account, "--", "ssh-keygen", "-q", "-t", "ed25519",
            "-N", "", "-f", str(action.private_path), "-C", "autophagy-repair-log",
        ))
    for path, mode in ((action.private_path, 0o600), (public_path, 0o644)):
        if not stat.S_ISREG(path.lstat().st_mode):
            raise OSError("repair-log keypair is not regular files")
        os.chmod(path, mode)
        shutil.chown(path, user=action.agent_account, group=action.agent_account)
    public = _public(public_path)
    # A conflicting binding must be resolved explicitly; duplicating it could leave
    # the same key authorized for a different command or an unrestricted session.
    for path in (action.authorized_path, action.known_hosts_path):
        if os.path.lexists(path) and not stat.S_ISREG(path.lstat().st_mode):
            raise OSError("repair-log SSH asset is not a regular file")
    authorized = (
        action.authorized_path.read_text(encoding="utf-8")
        if action.authorized_path.exists() else ""
    )
    if any(_carries_key(line, public) for line in authorized.splitlines()) and not _bound(authorized, public, action):
        raise OSError("repair-log key has a conflicting authorized_keys binding")
    addition = "" if _bound(authorized, public, action) else _binding(action) + public.line()
    _append(action.authorized_path, addition, action.ops_account)
    known = (
        action.known_hosts_path.read_text(encoding="utf-8")
        if action.known_hosts_path.exists() else ""
    )
    addition = "" if _host_known(known, host) else f"127.0.0.1 {host.line()}"
    _append(action.known_hosts_path, addition, action.agent_account)
