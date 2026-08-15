"""Command-line surface for roster validation."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, TypeAlias

import yaml

from .editor import RosterEditError, add_member, remove_member, save_roster
from .parser import load_roster
from .schema import MemberStatus, Roster, RosterMember
from .validator import RosterError

_Command: TypeAlias = Literal[
    "validate", "identity", "add-member", "remove-member", "peers-seed"
]
_TRUST_ROOT = Path("/etc/autophagy")


class _Arguments(argparse.Namespace):
    command: _Command
    path: Path
    name: str
    discord_user_id: str
    node_label: str
    output: Path | None

    def __init__(self) -> None:
        super().__init__()
        self.command = "validate"
        self.path = Path()
        self.name = ""
        self.discord_user_id = ""
        self.node_label = ""
        self.output = None


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m automation.group_roster")
    subcommands = parser.add_subparsers(dest="command", required=True)
    validate = subcommands.add_parser("validate", help="validate one roster YAML file")
    _ = validate.add_argument("path", type=Path)
    identity = subcommands.add_parser(
        "identity",
        help="render roster identity and member count for read-only health reporting",
    )
    _ = identity.add_argument("path", type=Path)
    add = subcommands.add_parser(
        "add-member",
        help="append one active member installation to a roster",
    )
    _ = add.add_argument("path", type=Path)
    _ = add.add_argument("--name", required=True)
    _ = add.add_argument("--discord-user-id", required=True)
    _ = add.add_argument("--node-label", required=True)
    remove = subcommands.add_parser(
        "remove-member",
        help="mark a member removed and emit deploy-key revocation requirements",
    )
    _ = remove.add_argument("path", type=Path)
    _ = remove.add_argument("--discord-user-id", required=True)
    peers_seed = subcommands.add_parser(
        "peers-seed",
        help="render a classification-only peers.yaml seed from one roster",
    )
    _ = peers_seed.add_argument("path", type=Path)
    _ = peers_seed.add_argument(
        "--output",
        type=Path,
        help="write the seed to this non-trust-root path instead of stdout",
    )
    return parser


def _render_peers_seed(roster: Roster) -> str:
    """Render the classification-only peers schema for every roster principal."""
    peers: dict[str, dict[str, str]] = {}
    entries = (
        (
            roster.admin.publisher_principal,
            roster.admin.discord_user_id,
            roster.admin.name,
            "agent",
        ),
        *(
            (member.node_label, member.discord_user_id, member.name, "peer")
            for member in roster.members
        ),
    )
    for agent_id, bot_user_id, bot_name, account in entries:
        if agent_id in peers:
            raise RosterEditError(
                f"PEERS-SEED: duplicate agent_id derived from roster: {agent_id}"
            )
        peers[agent_id] = {
            "bot_user_id": bot_user_id,
            "bot_name": bot_name,
            "account": account,
        }
    return yaml.safe_dump(
        {"version": 1, "peers": peers},
        allow_unicode=True,
        sort_keys=False,
    )


def _write_peers_seed(path: Path, document: str) -> None:
    """Write an explicitly requested classification seed outside the trust root."""
    destination = path.expanduser().resolve()
    if destination.is_relative_to(_TRUST_ROOT):
        raise RosterEditError(
            f"PEERS-SEED-WRITE: refusing attestation trust-root path: {destination}"
        )
    try:
        _ = destination.write_text(document, encoding="utf-8")
    except OSError as error:
        raise RosterEditError(
            f"PEERS-SEED-WRITE: cannot write {destination}: {error}"
        ) from error


def main(argv: Sequence[str] | None = None) -> int:
    """Run one roster validation or membership edit command."""
    arguments = _argument_parser().parse_args(argv, namespace=_Arguments())
    path = arguments.path
    try:
        roster = load_roster(path)
        match arguments.command:  # noqa: MATCH_OK — _Command is exhaustive.
            case "validate":
                channel = (
                    roster.update_channel
                    if roster.update_channel is not None
                    else "upstream"
                )
                announce = (
                    roster.announce_channel_id
                    if roster.announce_channel_id is not None
                    else "none"
                )
                fields = (
                    f"group_id={roster.group_id}",
                    f"members={len(roster.members)}",
                    f"update_channel={channel}",
                    f"announce_channel={announce}",
                )
                print(f"ROSTER-VALID {' '.join(fields)}")
                return 0
            case "identity":
                group_id = json.dumps(roster.group_id, ensure_ascii=True, separators=(",", ":"))
                print(f"ROSTER-IDENTITY group_id={group_id} members={len(roster.members)}")
                return 0
            case "add-member":
                edit = add_member(
                    roster,
                    RosterMember(
                        name=arguments.name,
                        discord_user_id=arguments.discord_user_id,
                        node_label=arguments.node_label,
                        status=MemberStatus.ACTIVE,
                    ),
                )
                save_roster(path, edit.roster)
                print(
                    " ".join(
                        (
                            "ROSTER-MEMBER-ADDED",
                            f"group_id={roster.group_id}",
                            f"discord_user_id={edit.member.discord_user_id}",
                            f"node_label={edit.member.node_label}",
                            "status=active",
                        )
                    )
                )
                print(
                    " ".join(
                        (
                            "DEPLOY-KEY-REGISTRATION-REQUIRED",
                            f"node_label={edit.member.node_label}",
                            "access=read-only",
                        )
                    )
                )
                return 0
            case "remove-member":
                edit = remove_member(roster, arguments.discord_user_id)
                if edit.changed:
                    save_roster(path, edit.roster)
                print(
                    " ".join(
                        (
                            "ROSTER-MEMBER-REMOVED",
                            f"group_id={roster.group_id}",
                            f"discord_user_id={edit.member.discord_user_id}",
                            f"node_label={edit.member.node_label}",
                            "status=removed",
                        )
                    )
                )
                print(
                    " ".join(
                        (
                            "DEPLOY-KEY-REVOCATION-REQUIRED",
                            f"node_label={edit.member.node_label}",
                            "access=read-only",
                        )
                    )
                )
                print("ROSTER-REVOCATION-READY status=removed publish=signed-roster")
                print("REMOTE-RECALL-LIMIT mounted-skills=unchanged owner-removal-required=true")
                return 0
            case "peers-seed":
                document = _render_peers_seed(roster)
                if arguments.output is None:
                    print(document, end="")
                    return 0
                _write_peers_seed(arguments.output, document)
                print(
                    " ".join(
                        (
                            "PEERS-SEED-WRITTEN",
                            f"path={arguments.output}",
                            f"entries={len(roster.members) + 1}",
                            "classification_only=true",
                        )
                    )
                )
                return 0
    except RosterEditError as error:
        print(f"ROSTER-EDIT-REFUSED: {error}", file=sys.stderr)
        return 2
    except RosterError as error:
        print(f"ROSTER-INVALID: {error}", file=sys.stderr)
        return 2
