"""Atomic roster membership edits with D1.3's non-recall boundary preserved.

Removing a member records ``status: removed`` for signed roster publication.
The administrator must separately revoke that installation's remote deploy key.
Neither action can reach into the member-owned node or detach an already-mounted
skill; managed-sync revocations remain digest-scoped and SI-7 stays read-only.
"""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from automation.typing_compat import override

import yaml

from .schema import MemberStatus, Roster, RosterMember
from .validator import YamlMapping, validate_roster


@dataclass(frozen=True, slots=True)
class RosterEditError(Exception):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class MemberEdit:
    roster: Roster
    member: RosterMember
    changed: bool


def _payload(roster: Roster) -> YamlMapping:
    payload: YamlMapping = {
        "schema": roster.schema,
        "group_id": roster.group_id,
        "admin": {
            "name": roster.admin.name,
            "discord_user_id": roster.admin.discord_user_id,
            "publisher_principal": roster.admin.publisher_principal,
            "signing_public_key": roster.admin.signing_public_key,
        },
        "members": [
            {
                "name": member.name,
                "discord_user_id": member.discord_user_id,
                "node_label": member.node_label,
                "status": member.status.value,
            }
            for member in roster.members
        ],
    }
    if roster.update_channel is not None:
        payload["update_channel"] = roster.update_channel
    if roster.announce_channel_id is not None:
        payload["announce_channel_id"] = roster.announce_channel_id
    if roster.revision is not None:
        payload["revision"] = roster.revision
    return payload


def _validated(roster: Roster) -> Roster:
    return validate_roster(_payload(roster))


def add_member(roster: Roster, member: RosterMember) -> MemberEdit:
    """Append one active installation while rejecting identity reuse."""
    known_ids = {
        roster.admin.discord_user_id,
        *(existing.discord_user_id for existing in roster.members),
    }
    if member.discord_user_id in known_ids:
        raise RosterEditError(
            f"MEMBER-EXISTS: discord_user_id {member.discord_user_id} is already present"
        )
    candidate = replace(roster, members=[*roster.members, member])
    validated = _validated(candidate)
    return MemberEdit(validated, validated.members[-1], True)


def remove_member(roster: Roster, discord_user_id: str) -> MemberEdit:
    """Record one member revocation without claiming remote node control."""
    updated: list[RosterMember] = []
    removed: RosterMember | None = None
    changed = False
    for member in roster.members:
        if member.discord_user_id != discord_user_id:
            updated.append(member)
            continue
        removed = replace(member, status=MemberStatus.REMOVED)
        changed = member.status is not MemberStatus.REMOVED
        updated.append(removed)
    if removed is None:
        raise RosterEditError(
            f"MEMBER-NOT-FOUND: discord_user_id {discord_user_id} is not in the roster"
        )
    validated = _validated(replace(roster, members=updated))
    return MemberEdit(validated, removed, changed)


def render_roster(roster: Roster) -> str:
    """Render the exact schema-v1 mapping accepted by the strict parser."""
    return yaml.safe_dump(
        _payload(roster),
        allow_unicode=True,
        sort_keys=False,
    )


def save_roster(path: Path, roster: Roster) -> None:
    """Atomically replace an existing regular roster file with validated YAML."""
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RosterEditError(f"ROSTER-WRITE: cannot inspect {path}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise RosterEditError(
            f"ROSTER-WRITE: refusing non-regular roster path: {path}"
        )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            _ = temporary.write(render_roster(roster))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, stat.S_IMODE(metadata.st_mode))
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as error:
        raise RosterEditError(f"ROSTER-WRITE: cannot replace {path}: {error}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
