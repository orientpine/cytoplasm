from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from automation.group_roster import MemberStatus, load_roster
from automation.group_roster.cli import main as roster_main
from automation.managed_sync.fetch import ManagedFetchError, sync_remote

_SIGNING_PUBLIC_KEY = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g "
    "group-admin"
)


@dataclass(frozen=True, slots=True)
class _FetchConfig:
    remote_url: str
    mirror_dir: Path
    ssh_key_path: Path


@dataclass(frozen=True, slots=True)
class _ScenarioResult:
    joined_status: MemberStatus
    first_fetch_cloned: bool
    first_release_present: bool
    removed_status: MemberStatus
    rejected_error: str


def _run(command: tuple[str, ...], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_roster(path: Path) -> None:
    _ = path.write_text(
        "\n".join(
            (
                "schema: 1",
                "group_id: fixture-lab",
                "admin:",
                "  name: Fixture Admin",
                '  discord_user_id: "1001"',
                "  publisher_principal: publisher-fixture-admin@autophagy",
                f"  signing_public_key: {_SIGNING_PUBLIC_KEY}",
                "members: []",
                "",
            )
        ),
        encoding="utf-8",
    )


def _install_fake_ssh(path: Path) -> None:
    source = """import os
import shlex
import subprocess
import sys
from pathlib import Path

arguments = sys.argv[1:]
key_path = Path(arguments[arguments.index('-i') + 1])
authorized = Path(os.environ['FIXTURE_AUTHORIZED_DEPLOY_KEY'])
if not authorized.is_file() or not key_path.is_file():
    print('Permission denied (publickey).', file=sys.stderr)
    raise SystemExit(255)
if authorized.read_bytes() != key_path.read_bytes():
    print('Permission denied (publickey).', file=sys.stderr)
    raise SystemExit(255)
remote_command = shlex.split(arguments[-1])
if len(remote_command) != 2 or remote_command[0] != 'git-upload-pack':
    raise SystemExit(2)
completed = subprocess.run(
    ('git-upload-pack', remote_command[1]),
    stdin=sys.stdin.buffer,
    stdout=sys.stdout.buffer,
    stderr=sys.stderr.buffer,
    check=False,
)
raise SystemExit(completed.returncode)
"""
    _ = path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    path.chmod(0o755)


def _seed_remote(root: Path) -> tuple[Path, Path]:
    remote = root / "group-skills.git"
    admin = root / "admin"
    _ = _run(("git", "init", "--bare", str(remote)))
    _ = _run(("git", "init", str(admin)))
    _ = _run(("git", "config", "user.name", "Fixture Admin"), admin)
    _ = _run(
        ("git", "config", "user.email", "publisher-fixture-admin@autophagy"),
        admin,
    )
    _ = (admin / "README.md").write_text("release 1\n", encoding="utf-8")
    _ = _run(("git", "add", "README.md"), admin)
    _ = _run(("git", "commit", "-m", "release 1"), admin)
    _ = _run(("git", "branch", "-M", "main"), admin)
    _ = _run(("git", "remote", "add", "origin", str(remote)), admin)
    _ = _run(("git", "tag", "managed-demo/v1"), admin)
    _ = _run(("git", "push", "-u", "origin", "main", "--tags"), admin)
    _ = _run(("git", "checkout", "-b", "roster"), admin)
    roster_dir = admin / "roster"
    roster_dir.mkdir()
    _ = (roster_dir / "roster.yaml").write_text("fixture roster\n", encoding="utf-8")
    _ = (roster_dir / "roster.yaml.sig").write_text("fixture signature\n", encoding="utf-8")
    _ = _run(("git", "add", "roster"), admin)
    _ = _run(("git", "commit", "-m", "publish roster"), admin)
    _ = _run(("git", "push", "-u", "origin", "roster"), admin)
    _ = _run(("git", "checkout", "main"), admin)
    _ = _run(("git", "symbolic-ref", "HEAD", "refs/heads/main"), remote)
    return remote, admin


def _publish_second_release(admin: Path) -> None:
    _ = (admin / "README.md").write_text("release 2\n", encoding="utf-8")
    _ = _run(("git", "add", "README.md"), admin)
    _ = _run(("git", "commit", "-m", "release 2"), admin)
    _ = _run(("git", "tag", "managed-demo/v2"), admin)
    _ = _run(("git", "push", "origin", "main", "--tags"), admin)


def _run_scenario(root: Path, monkeypatch: pytest.MonkeyPatch) -> _ScenarioResult:
    roster = root / "roster.yaml"
    deploy_key = root / "member-deploy-key"
    authorized_key = root / "remote-authorized-deploy-key"
    fake_bin = root / "bin"
    fake_bin.mkdir()
    _install_fake_ssh(fake_bin / "ssh")
    _write_roster(roster)
    _ = deploy_key.write_text("fixture member deploy private key\n", encoding="utf-8")
    _ = authorized_key.write_bytes(deploy_key.read_bytes())
    remote, admin = _seed_remote(root)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("FIXTURE_AUTHORIZED_DEPLOY_KEY", str(authorized_key))

    add_code = roster_main(
        (
            "add-member",
            str(roster),
            "--name",
            "Fixture Member",
            "--discord-user-id",
            "1002",
            "--node-label",
            "fixture-member-node",
        )
    )
    if add_code != 0:
        raise AssertionError(f"fixture add-member failed with rc={add_code}")
    joined_status = load_roster(roster).members[0].status
    config = _FetchConfig(
        remote_url=f"member@fixture:{remote}",
        mirror_dir=root / "member" / "mirror",
        ssh_key_path=deploy_key,
    )
    first = sync_remote(config)
    tags = _run(("git", "tag", "--list"), config.mirror_dir).stdout.splitlines()

    remove_code = roster_main(
        ("remove-member", str(roster), "--discord-user-id", "1002")
    )
    if remove_code != 0:
        raise AssertionError(f"fixture remove-member failed with rc={remove_code}")
    authorized_key.unlink()
    _publish_second_release(admin)
    try:
        _ = sync_remote(config)
    except ManagedFetchError as error:
        rejected_error = str(error)
    else:
        rejected_error = ""
    return _ScenarioResult(
        joined_status=joined_status,
        first_fetch_cloned=first.cloned,
        first_release_present="managed-demo/v1" in tags,
        removed_status=load_roster(roster).members[0].status,
        rejected_error=rejected_error,
    )


def test_group_join_leave_when_deploy_key_is_revoked_then_new_fetch_authentication_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    root = tmp_path / "fixture"
    root.mkdir()

    # When
    result = _run_scenario(root, monkeypatch)

    # Then
    assert result.joined_status is MemberStatus.ACTIVE
    assert result.first_fetch_cloned is True
    assert result.first_release_present is True
    assert result.removed_status is MemberStatus.REMOVED
    assert "Permission denied (publickey)" in result.rejected_error
