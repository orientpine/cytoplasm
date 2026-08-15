from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Protocol

from automation.install.plan import EnsurePeerAttestKey


class CommandRunner(Protocol):
    def __call__(
        self,
        command: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


class FileWriter(Protocol):
    def __call__(
        self,
        path: Path,
        content: str,
        mode: int,
        owner: str,
        group: str,
    ) -> None: ...


def ensure_peer_attest_key(
    action: EnsurePeerAttestKey,
    run: CommandRunner,
    write_file: FileWriter,
) -> None:
    generated_public = action.private_path.with_suffix(".pub")
    action.private_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(action.private_path.parent, 0o700)
    shutil.chown(action.private_path.parent, user=action.owner, group=action.owner)

    private_exists = os.path.lexists(action.private_path)
    public_exists = os.path.lexists(generated_public)
    if private_exists != public_exists:
        raise OSError("peer attestation keypair is partial; refusing regeneration")
    if not private_exists:
        _ = run(
            (
                "runuser",
                "-u",
                action.owner,
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

    try:
        private_stat = action.private_path.lstat()
        public_stat = generated_public.lstat()
    except OSError as error:
        raise OSError("peer attestation keypair is incomplete") from error
    if not stat.S_ISREG(private_stat.st_mode) or not stat.S_ISREG(public_stat.st_mode):
        raise OSError("peer attestation keypair is not regular files")

    os.chmod(action.private_path, 0o600)
    os.chmod(generated_public, 0o644)
    shutil.chown(action.private_path, user=action.owner, group=action.owner)
    shutil.chown(generated_public, user=action.owner, group=action.owner)
    public_key = generated_public.read_text(encoding="utf-8")
    if not public_key.endswith("\n") or public_key.count("\n") != 1:
        raise OSError("peer attestation public key is not one canonical line")
    write_file(action.public_path, public_key, 0o644, "root", "root")
