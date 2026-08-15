from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


ROSTER_NAMESPACE = "autophagy-roster"
ROSTER_PRINCIPAL = "publisher-testlab@autophagy"


@dataclass(frozen=True, slots=True)
class FeedConfig:
    remote_url: str
    mirror_dir: Path
    ssh_key_path: Path


@dataclass(frozen=True, slots=True)
class RosterRepository:
    publisher: Path
    private_key: Path
    allowed_signers: Path
    destination: Path
    feed_config: FeedConfig

    @property
    def public_key(self) -> str:
        return self.private_key.with_suffix(".pub").read_text(encoding="utf-8").strip()


def _run(command: tuple[str, ...]) -> None:
    _ = subprocess.run(command, check=True, capture_output=True)


def _git(repository: Path, command: tuple[str, ...]) -> None:
    _run(("git", "-C", str(repository), *command))


def _commit_and_push(repository: RosterRepository) -> None:
    _git(repository.publisher, ("add", "-A", "roster"))
    _git(repository.publisher, ("commit", "-m", "update roster"))
    _git(repository.publisher, ("push", "origin", "HEAD:refs/heads/roster"))


def roster_bytes(repository: RosterRepository, member_ids: tuple[str, ...]) -> bytes:
    members = "".join(
        (
            "  - name: Test Member\n"
            f'    discord_user_id: "{member_id}"\n'
            f"    node_label: node-{member_id}\n"
            "    status: active\n"
        )
        for member_id in member_ids
    )
    rendered_members = f"members:\n{members}" if members else "members: []\n"
    return (
        "schema: 1\n"
        "group_id: testlab\n"
        "admin:\n"
        "  name: Test Admin\n"
        '  discord_user_id: "2001"\n'
        f"  publisher_principal: {ROSTER_PRINCIPAL}\n"
        f"  signing_public_key: {repository.public_key}\n"
        f"{rendered_members}"
    ).encode()


def publish_signed_roster(
    repository: RosterRepository,
    payload: bytes,
    namespace: str = ROSTER_NAMESPACE,
) -> None:
    roster_path = repository.publisher / "roster" / "roster.yaml"
    signature_path = repository.publisher / "roster" / "roster.yaml.sig"
    roster_path.parent.mkdir(exist_ok=True)
    _ = roster_path.write_bytes(payload)
    signature_path.unlink(missing_ok=True)
    _run(
        (
            "ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(repository.private_key),
            "-n",
            namespace,
            str(roster_path),
        )
    )
    _commit_and_push(repository)


def publish_tampered_roster(repository: RosterRepository, payload: bytes) -> None:
    _ = (repository.publisher / "roster" / "roster.yaml").write_bytes(payload)
    _commit_and_push(repository)


def publish_unsigned_roster(repository: RosterRepository, payload: bytes) -> None:
    roster_path = repository.publisher / "roster" / "roster.yaml"
    _ = roster_path.write_bytes(payload)
    roster_path.with_suffix(".yaml.sig").unlink(missing_ok=True)
    _commit_and_push(repository)


def create_roster_repository(tmp_path: Path) -> RosterRepository:
    remote = tmp_path / "remote.git"
    publisher = tmp_path / "publisher"
    private_key = tmp_path / "roster_signing_key"
    allowed_signers = tmp_path / "allowed_signers"
    subscriber = tmp_path / "subscriber"
    publisher.mkdir()
    subscriber.mkdir()
    _run(("git", "init", "--bare", "--initial-branch=main", str(remote)))
    _run(("git", "init", "--initial-branch=main", str(publisher)))
    _git(publisher, ("config", "user.name", "Roster Publisher"))
    _git(publisher, ("config", "user.email", "publisher@test.invalid"))
    _ = (publisher / "README.md").write_text("managed feed\n", encoding="utf-8")
    _git(publisher, ("add", "README.md"))
    _git(publisher, ("commit", "-m", "initialize feed"))
    _git(publisher, ("remote", "add", "origin", str(remote)))
    _git(publisher, ("push", "-u", "origin", "main"))
    _git(publisher, ("switch", "-c", "roster"))
    _run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private_key)))
    repository = RosterRepository(
        publisher=publisher,
        private_key=private_key,
        allowed_signers=allowed_signers,
        destination=subscriber / "roster.yaml",
        feed_config=FeedConfig(
            remote_url=str(remote),
            mirror_dir=subscriber / "mirror",
            ssh_key_path=subscriber / "deploy_key",
        ),
    )
    _ = allowed_signers.write_text(
        f'{ROSTER_PRINCIPAL} namespaces="{ROSTER_NAMESPACE}" {repository.public_key}\n',
        encoding="utf-8",
    )
    return repository
