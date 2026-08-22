"""The clone requires a host key the installer never seeds — say so by name.

`SystemMutator._repository` clones with `StrictHostKeyChecking=yes` under the
ops account's HOME. That is the right setting, but nothing in the installer puts
the origin's host key into `~ops/.ssh/known_hosts`, so on a fresh node the clone
dies inside ssh with a message the operator sees as `EnsureRepository failed:
CalledProcessError` and nothing else.

Seeding it automatically is deliberately NOT done: that would make the installer
decide, on the operator's behalf, which host key is genuine — the one decision
`docs/guide/install.md` §6.2 asks a human to make with `ssh-keyscan` plus an
out-of-band fingerprint comparison. So the absence is only *named*, before the
clone runs, and the named prerequisite survives all the way into the report.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from automation.install.checks import Status
from automation.install.executor import ExecutionContext, RealExecutor
from automation.install.known_hosts import KNOWN_HOSTS_PREREQUISITE, missing_known_host
from automation.install.plan import EnsureRepository
from automation.node_config import default_node_config


def _config(tmp_path: Path, origin_url: str):
    return replace(
        default_node_config(),
        origin_url=origin_url,
        ops_home=tmp_path / "ops",
        ops_account="ops",
    )


def _known_hosts(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "ops" / ".ssh" / "known_hosts"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(content, encoding="utf-8")
    return path


def test_absent_known_hosts_is_named_as_a_prerequisite(tmp_path: Path) -> None:
    # Given a fresh node: no ~ops/.ssh/known_hosts at all
    reason = missing_known_host("git@github.com:orientpine/x.git", tmp_path / "ops")

    # Then
    assert reason is not None
    assert KNOWN_HOSTS_PREREQUISITE in reason
    assert "github.com" in reason
    assert "ssh-keyscan" in reason


def test_known_hosts_without_the_origin_host_is_named_too(tmp_path: Path) -> None:
    # Given a known_hosts that knows some other host
    _ = _known_hosts(tmp_path, "gitlab.example ssh-ed25519 AAAAC3Nz\n")

    # When
    reason = missing_known_host("ssh://git@github.com/orientpine/x.git", tmp_path / "ops")

    # Then
    assert reason is not None
    assert "github.com" in reason


def test_a_seeded_host_key_satisfies_the_prerequisite(tmp_path: Path) -> None:
    # Given the operator ran ssh-keyscan as §6.2 asks
    _ = _known_hosts(tmp_path, "github.com ssh-ed25519 AAAAC3Nz\n")

    # When / Then
    assert missing_known_host("git@github.com:orientpine/x.git", tmp_path / "ops") is None


def test_bracketed_nonstandard_port_entry_is_recognised(tmp_path: Path) -> None:
    # Given the form OpenSSH writes for a non-default port
    _ = _known_hosts(tmp_path, "[git.example.net]:2222 ssh-ed25519 AAAAC3Nz\n")

    # When / Then
    assert missing_known_host("ssh://git@git.example.net:2222/x.git", tmp_path / "ops") is None


def test_hashed_known_hosts_is_not_reported_as_missing(tmp_path: Path) -> None:
    # Given HashKnownHosts=yes output, which cannot be matched by name.
    # Claiming "missing" here would be a false prerequisite that blocks a node
    # whose host key is in fact present, so we defer to ssh.
    _ = _known_hosts(tmp_path, "|1|abc=|def= ssh-ed25519 AAAAC3Nz\n")

    # When / Then
    assert missing_known_host("git@github.com:orientpine/x.git", tmp_path / "ops") is None


def test_a_local_origin_needs_no_host_key(tmp_path: Path) -> None:
    # Given file:// and absolute-path remotes, which the fixtures use
    assert missing_known_host("/srv/fixture/origin.git", tmp_path / "ops") is None
    assert missing_known_host("file:///srv/fixture/origin.git", tmp_path / "ops") is None


def test_https_origin_needs_no_host_key(tmp_path: Path) -> None:
    assert missing_known_host("https://github.com/orientpine/x.git", tmp_path / "ops") is None


def test_repository_action_refuses_before_running_git(tmp_path: Path) -> None:
    # Given a node whose ops account has no known_hosts
    config = _config(tmp_path, "git@github.com:orientpine/x.git")
    executor = RealExecutor(ExecutionContext(config, Path.cwd(), Path("/absent"), None))
    calls: list[tuple[str, ...]] = []

    def record(
        command: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del env, cwd
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    # When
    action = EnsureRepository(tmp_path / "checkout", config.origin_url, tmp_path / "key")
    executor._mutator.run = record  # pyright: ignore[reportAttributeAccessIssue]
    (result,) = executor.execute(action)

    # Then no clone was attempted and the report names the prerequisite
    assert calls == []
    assert result.status is Status.FAIL
    assert KNOWN_HOSTS_PREREQUISITE in result.detail


def test_the_installer_never_writes_the_host_key_itself(tmp_path: Path) -> None:
    # Given — auto-seeding is the one thing this guard must not start doing:
    # trusting a host key is the operator's decision (install.md §6.2), not ours.
    source = Path("automation/install/known_hosts.py").read_text(encoding="utf-8")

    # Then the module can only read, and it names the manual step instead of taking it
    assert "import subprocess" not in source
    assert "subprocess." not in source
    assert "write_text" not in source
    assert "ssh-keyscan" in source
    assert not (tmp_path / "ops" / ".ssh" / "known_hosts").exists()


@pytest.mark.parametrize(
    "origin_url",
    ["git@github.com:x/y.git", "ssh://git@github.com/x/y.git", "ssh://github.com:22/x/y.git"],
)
def test_every_ssh_form_resolves_the_same_host(tmp_path: Path, origin_url: str) -> None:
    _ = _known_hosts(tmp_path, "github.com ssh-ed25519 AAAAC3Nz\n")
    assert missing_known_host(origin_url, tmp_path / "ops") is None
