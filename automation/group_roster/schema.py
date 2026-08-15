"""Immutable research-group roster value types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

SCHEMA_VERSION: Final = 1


class MemberStatus(StrEnum):
    """Membership lifecycle states supported by roster schema v1."""

    ACTIVE = "active"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class RosterAdmin:
    """The single administrator and managed-skill publisher for a group."""

    name: str
    discord_user_id: str
    publisher_principal: str
    signing_public_key: str


@dataclass(frozen=True, slots=True)
class RosterMember:
    """One independently operated member installation."""

    name: str
    discord_user_id: str
    node_label: str
    status: MemberStatus


@dataclass(frozen=True, slots=True)
class Roster:
    """One schema-v1 research group roster."""

    schema: Literal[1]
    group_id: str
    admin: RosterAdmin
    members: list[RosterMember]
    update_channel: str | None = None
    announce_channel_id: str | None = None
    # Monotonic publication counter. A signature proves who wrote a roster, never
    # WHEN — so replaying an older genuinely-signed roster would otherwise undo a
    # revocation. `refresh_roster` refuses any fetched roster that does not advance
    # this. Optional so pre-revision installations keep working unchanged; once an
    # installation holds a revisioned roster, dropping the field is a downgrade.
    revision: int | None = None

    def sender_id_for_discord_author(self, author_id: str) -> str | None:
        """Return the active roster principal bound to a Discord author."""
        if self.admin.discord_user_id == author_id:
            return self.admin.publisher_principal
        member = next(
            (
                candidate
                for candidate in self.members
                if candidate.discord_user_id == author_id
            ),
            None,
        )
        if member is None or member.status is not MemberStatus.ACTIVE:
            return None
        return member.node_label
