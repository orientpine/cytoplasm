"""Regression: GIT_SSH_COMMAND must shell-quote the configured ssh key path.

F2 (security audit, 2026-08-15). Three sites built ``GIT_SSH_COMMAND`` by raw
f-string interpolation of ``ssh_key_path``:

* ``automation/managed_sync/fetch.py``
* ``automation/rag_ingest/sources/obsidian.py``
* ``automation/obsidian_write/writer.py``

git executes ``GIT_SSH_COMMAND`` **through a shell**, and every one of those key
paths is read from agent-writable ``~/.hermes/**`` config, so a path such as
``/tmp/k; touch PWNED; #`` was arbitrary command execution.

Each site is asserted twice: the command must word-split back into exactly the
intended argv (``shlex.split`` is POSIX shell word-splitting), and the audit's
payload must not fire when the command is genuinely handed to ``/bin/sh``.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

from automation.managed_sync.fetch import sync_roster_ref
from automation.obsidian_write.config import ObsidianWriteConfig, ObsidianWriteError
from automation.obsidian_write.writer import write_note
from automation.obsidian_write.note import NotePlan
from automation.rag_ingest.config import ObsidianSourceConfig
from automation.rag_ingest.sources.obsidian import sync_mirror

# The audit's payload: a key path that closes the -i argument, runs a command,
# and comments out the rest of the line.
MARKER = "PWNED"
PAYLOAD_NAME = f"k; touch {MARKER}; #"


@dataclass(frozen=True, slots=True)
class Captured:
    argv: tuple[str, ...]
    env: dict[str, str]


class _Recorder:
    """Captures every invocation and optionally fails one, without running git."""

    def __init__(self, *, fail_at: int | None = None) -> None:
        self.calls: list[Captured] = []
        self._fail_at: int | None = fail_at

    def _record(
        self,
        args: list[str],
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(Captured(tuple(args), dict(env)))
        code = 1 if self._fail_at == len(self.calls) else 0
        return subprocess.CompletedProcess(args, code, stdout="", stderr="")


class RecordingRunner(_Recorder):
    """Matches managed_sync.GitRunner and obsidian_write's runner protocol."""

    def __call__(
        self,
        args: list[str],
        /,
        *,
        cwd: Path | None = None,
        env: dict[str, str],
        capture_output: bool = False,
        text: bool = False,
        timeout: float = 0.0,
    ) -> subprocess.CompletedProcess[str]:
        return self._record(args, env)


class RagIngestRunner(_Recorder):
    """rag_ingest's protocol names the first parameter, so it cannot be renamed."""

    def __call__(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str],
        timeout: float = 0.0,
    ) -> subprocess.CompletedProcess[str]:
        return self._record(args, env)


@dataclass(frozen=True, slots=True)
class _RosterRemote:
    mirror_dir: Path
    ssh_key_path: Path


def _assert_quoted(command: str, key_path: Path) -> None:
    """The shell must see the key path as exactly one word."""
    assert shlex.split(command) == ["ssh", "-i", str(key_path), "-o", "IdentitiesOnly=yes"]


def _assert_payload_does_not_fire(command: str, tmp_path: Path) -> None:
    """Hand the command to a real /bin/sh, exactly as git would."""
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir(exist_ok=True)
    stub_ssh = stub_dir / "ssh"
    _ = stub_ssh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub_ssh.chmod(0o755)
    workdir = tmp_path / "shell-cwd"
    workdir.mkdir(exist_ok=True)

    # The stub shadows ssh; os.defpath keeps the payload's own command (touch)
    # resolvable, so a failure to fire means quoting worked — not a missing binary.
    _ = subprocess.run(
        ["/bin/sh", "-c", command],
        cwd=workdir,
        env={"PATH": f"{stub_dir}{os.defpath}"},
        capture_output=True,
        check=False,
    )

    assert not (workdir / MARKER).exists(), "injected command executed through the shell"


def _malicious_key(tmp_path: Path, *, create: bool = False) -> Path:
    key = tmp_path / PAYLOAD_NAME
    if create:
        _ = key.write_text("not-a-real-key\n", encoding="utf-8")
    return key


def test_managed_sync_quotes_the_key_path(tmp_path: Path) -> None:
    # Given: managed-sync config (agent-writable) with the audit's payload path.
    key = _malicious_key(tmp_path)
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    runner = RecordingRunner()

    # When: the roster ref is fetched.
    sync_roster_ref(_RosterRemote(mirror_dir=mirror, ssh_key_path=key), runner)

    # Then: the shell sees one argument, and the payload never runs.
    command = runner.calls[0].env["GIT_SSH_COMMAND"]
    _assert_quoted(command, key)
    _assert_payload_does_not_fire(command, tmp_path)


def test_rag_ingest_obsidian_quotes_the_key_path(tmp_path: Path) -> None:
    # Given: rag-ingest config (agent-writable) with the audit's payload path.
    key = _malicious_key(tmp_path)
    config = ObsidianSourceConfig(
        enabled=True,
        repo_url="git@example.invalid:cha/vault.git",
        mirror_dir=tmp_path / "mirror",
        ssh_key_path=key,
        sensitivity_rules_path=tmp_path / "sensitivity.yaml",
    )
    runner = RagIngestRunner()

    # When: the mirror is synced (no .git yet, so this clones).
    _ = sync_mirror(config, runner)

    command = runner.calls[0].env["GIT_SSH_COMMAND"]
    _assert_quoted(command, key)
    _assert_payload_does_not_fire(command, tmp_path)


def test_obsidian_write_quotes_the_key_path(tmp_path: Path) -> None:
    # Given: obsidian-write config (agent-writable) with the audit's payload path.
    key = _malicious_key(tmp_path, create=True)
    config = ObsidianWriteConfig(
        repo_url="git@example.invalid:cha/vault.git",
        clone_dir=tmp_path / "write-clone",
        ssh_key_path=key,
        branch="main",
    )
    plan = NotePlan(PurePosixPath("2_Areas/note.md"), "note", "body")
    runner = RecordingRunner(fail_at=1)

    # When: the first git step runs and then fails, before any push.
    with pytest.raises(ObsidianWriteError):
        _ = write_note(plan, config, runner)

    command = runner.calls[0].env["GIT_SSH_COMMAND"]
    _assert_quoted(command, key)
    _assert_payload_does_not_fire(command, tmp_path)


@pytest.mark.parametrize(
    "name",
    ["id_ed25519", "obsidian_write_key", "managed-sync.key", "key.pem"],
)
def test_ordinary_key_paths_are_left_unquoted(tmp_path: Path, name: str) -> None:
    # Given: a legitimate key path — quoting must not change the emitted command.
    key = tmp_path / name
    mirror = tmp_path / "mirror"
    mirror.mkdir(exist_ok=True)
    runner = RecordingRunner()

    sync_roster_ref(_RosterRemote(mirror_dir=mirror, ssh_key_path=key), runner)

    assert (
        runner.calls[0].env["GIT_SSH_COMMAND"]
        == f"ssh -i {key} -o IdentitiesOnly=yes"
    )
