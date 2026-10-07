"""Build a local roster from the node's own trust roots (owner + agent/peer bots).

Values are read at run time from the peers registry, the managed-skills
allowed-signers file and the interop config; nothing is written to tracked files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn

import yaml

from automation.install.allowed_signers import (
    MANAGED_SKILLS_ALLOWED_SIGNERS_PATH,
    SignerEntry,
    TrustKeyError,
    parse_allowed_signers,
)
from automation.typing_compat import override

from .editor import render_roster
from .parser import DEFAULT_ROSTER_PATH, parse_roster
from .schema import SCHEMA_VERSION, MemberStatus, Roster, RosterAdmin, RosterMember
from .validator import YamlValue

DEFAULT_PEERS_PATH: Final = Path("/etc/autophagy/peers.yaml")
DEFAULT_INTEROP_CONFIG: Final = Path("~/.hermes/interop/config.json")
_PUBLISHER: Final = re.compile(
    r"\Apublisher-(?P<group>[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)@autophagy\Z"
)
_TOKEN: Final = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_LONG_DIGITS: Final = re.compile(r"\d{8,}")


@dataclass(frozen=True, slots=True)
class InitRefused(Exception):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class _Peer:
    account: str
    bot_user_id: str
    bot_name: str | None


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        raise InitRefused(f"usage: {message}")


class _Arguments(argparse.Namespace):
    peers: Path
    allowed_signers: Path
    interop_config: Path
    owner_id: str | None
    admin_name: str
    label: list[str] | None
    publisher: str | None
    group_id: str | None
    output: Path
    force: bool
    print_only: bool

    def __init__(self) -> None:
        super().__init__()
        self.peers = DEFAULT_PEERS_PATH
        self.allowed_signers = MANAGED_SKILLS_ALLOWED_SIGNERS_PATH
        self.interop_config = DEFAULT_INTEROP_CONFIG
        self.owner_id = None
        self.admin_name = "owner"
        self.label = None
        self.publisher = None
        self.group_id = None
        self.output = DEFAULT_ROSTER_PATH
        self.force = False
        self.print_only = False


def _parser() -> _Parser:
    parser = _Parser(prog="python3 -m automation.group_roster init-local")
    add = parser.add_argument
    _ = add("--peers", type=Path, default=DEFAULT_PEERS_PATH)
    _ = add("--allowed-signers", type=Path, default=MANAGED_SKILLS_ALLOWED_SIGNERS_PATH)
    _ = add("--interop-config", type=Path, default=DEFAULT_INTEROP_CONFIG, help="owner_id source")
    _ = add("--owner-id", help="owner Discord id; skips reading --interop-config")
    _ = add("--admin-name", default="owner")
    _ = add("--label", action="append", required=True, metavar="ACCOUNT=AGENT_ID",
            help="peers account whose bot becomes a member labelled AGENT_ID (repeat)")
    _ = add("--publisher", help="publisher principal; required with several signers")
    _ = add("--group-id", help="default: the <slug> of publisher-<slug>@autophagy")
    _ = add("--output", type=Path, default=DEFAULT_ROSTER_PATH)
    _ = add("--force", action="store_true", help="replace an existing roster")
    _ = add("--print", dest="print_only", action="store_true", help="print, write no file")
    return parser


def _read(path: Path) -> str:
    return path.expanduser().read_text(encoding="utf-8")


def _labels(raw: Sequence[str]) -> list[tuple[str, str]]:
    labels: list[tuple[str, str]] = []
    for entry in raw:
        account, separator, agent_id = entry.partition("=")
        if not separator or not _TOKEN.fullmatch(account) or not _TOKEN.fullmatch(agent_id):
            raise InitRefused(f"--label must be <account>=<agent_id>: {entry!r}")
        labels.append((account, agent_id))
    for index, name in ((0, "account"), (1, "agent_id")):
        values = [label[index] for label in labels]
        if len(values) != len(set(values)):
            raise InitRefused(f"--label repeats one {name}")
    return labels


def _peer(key: str, entry: YamlValue) -> _Peer:
    if not isinstance(entry, dict):
        raise InitRefused(f"peers entry {key} must be a mapping")
    account, bot_user_id, bot_name = (
        entry.get("account"), entry.get("bot_user_id"), entry.get("bot_name")
    )
    if not isinstance(account, str) or not account:
        raise InitRefused(f"peers entry {key} needs an account")
    if not isinstance(bot_user_id, str) or not (bot_user_id.isascii() and bot_user_id.isdecimal()):
        raise InitRefused(f"peers entry {key} needs a numeric string bot_user_id")
    if bot_name is not None and (not isinstance(bot_name, str) or not bot_name.strip()):
        raise InitRefused(f"peers entry {key} has an empty bot_name")
    return _Peer(account, bot_user_id, bot_name)


def _read_peers(path: Path) -> list[_Peer]:
    raw: YamlValue = yaml.safe_load(_read(path))
    if not isinstance(raw, dict):
        raise InitRefused(f"peers file {path} must be a mapping")
    version = raw.get("version")
    if type(version) is not int or version != 1:
        raise InitRefused(f"peers file {path} must declare version: 1")
    node = raw.get("peers")
    if not isinstance(node, dict) or not node:
        raise InitRefused(f"peers file {path} needs a non-empty peers mapping")
    return [_peer(str(key), entry) for key, entry in node.items()]


def _members(peers: list[_Peer], labels: list[tuple[str, str]]) -> list[RosterMember]:
    members: list[RosterMember] = []
    for account, agent_id in labels:
        matches = [peer for peer in peers if peer.account == account]
        if len(matches) != 1:
            raise InitRefused(
                f"--label {account}={agent_id} matches {len(matches)} peers entries, need 1"
            )
        peer = matches[0]
        members.append(
            RosterMember(
                name=peer.bot_name if peer.bot_name is not None else agent_id,
                discord_user_id=peer.bot_user_id,
                node_label=agent_id,
                status=MemberStatus.ACTIVE,
            )
        )
    return members


def _signer(path: Path, publisher: str | None) -> SignerEntry:
    entries = parse_allowed_signers(_read(path))
    candidates = [entry for entry in entries if _PUBLISHER.fullmatch(entry.principal)]
    if publisher is not None:
        candidates = [entry for entry in candidates if entry.principal == publisher]
    elif len({entry.principal for entry in candidates}) > 1:
        raise InitRefused("several publisher principals; choose one with --publisher")
    if len(candidates) != 1:
        raise InitRefused(f"need exactly one publisher signer line, found {len(candidates)}")
    if candidates[0].key.algorithm != "ssh-ed25519":
        raise InitRefused(f"publisher key must be ssh-ed25519, not {candidates[0].key.algorithm}")
    return candidates[0]


def _owner_id(arguments: _Arguments) -> str:
    if arguments.owner_id is not None:
        return arguments.owner_id
    raw: YamlValue = json.loads(_read(arguments.interop_config))
    owner = raw.get("owner_id") if isinstance(raw, dict) else None
    if isinstance(owner, int) and not isinstance(owner, bool):
        owner = str(owner)
    if not isinstance(owner, str) or not owner:
        raise InitRefused(f"{arguments.interop_config} has no owner_id")
    return owner


def build_roster(arguments: _Arguments) -> tuple[Roster, str]:
    """Assemble the roster and prove it survives the strict parser unchanged."""
    labels = _labels(arguments.label or [])
    members = _members(_read_peers(arguments.peers), labels)
    signer = _signer(arguments.allowed_signers, arguments.publisher)
    match = _PUBLISHER.fullmatch(signer.principal)
    group_id = arguments.group_id or (match.group("group") if match else "")
    roster = Roster(
        schema=SCHEMA_VERSION,
        group_id=group_id,
        admin=RosterAdmin(
            name=arguments.admin_name,
            discord_user_id=_owner_id(arguments),
            publisher_principal=signer.principal,
            signing_public_key=f"{signer.key.algorithm} {signer.key.material}",
        ),
        members=members,
        revision=1,
    )
    document = render_roster(roster)
    if parse_roster(document, source="<init-local>") != roster:
        raise InitRefused("rendered roster does not round-trip unchanged")
    return roster, document


def _write(path: Path, document: str, *, force: bool) -> None:
    if os.path.lexists(path) and not force:
        raise InitRefused(f"{path} already exists; pass --force to replace it")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary: Path | None = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            _ = handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _mask(user_id: str) -> str:
    return f"…{user_id[-4:]}"


def _summary(path: Path, roster: Roster) -> str:
    members = ",".join(
        f"{member.node_label}:{_mask(member.discord_user_id)}" for member in roster.members
    )
    return " ".join(
        (
            "ROSTER-INIT-WRITTEN",
            f"path={path}",
            f"group_id={roster.group_id}",
            f"admin={_mask(roster.admin.discord_user_id)}",
            f"members={members}",
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``init-local``; refusals print one masked line and return 2."""
    try:
        arguments = _parser().parse_args(argv, namespace=_Arguments())
        roster, document = build_roster(arguments)
        if arguments.print_only:
            print(document, end="")
            return 0
        output = arguments.output.expanduser()
        _write(output, document, force=arguments.force)
    except (InitRefused, TrustKeyError, OSError, ValueError, yaml.YAMLError) as error:
        detail = _LONG_DIGITS.sub(lambda found: _mask(found.group()), str(error))
        print(f"ROSTER-INIT-REFUSED: {detail}", file=sys.stderr)
        return 2
    print(_summary(output, roster))
    return 0
