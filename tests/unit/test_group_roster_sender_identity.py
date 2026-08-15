from __future__ import annotations

import pytest

from automation.group_roster.schema import MemberStatus, Roster, RosterAdmin, RosterMember


_ADMIN_ID = "100000000000000001"
_ACTIVE_ID = "100000000000000002"
_REMOVED_ID = "100000000000000003"
_ROSTER = Roster(
    schema=1,
    group_id="test-group",
    admin=RosterAdmin(
        name="Test Admin",
        discord_user_id=_ADMIN_ID,
        publisher_principal="publisher-test-admin@autophagy",
        signing_public_key="unused-in-typed-fixture",
    ),
    members=[
        RosterMember("Active", _ACTIVE_ID, "active-node", MemberStatus.ACTIVE),
        RosterMember("Removed", _REMOVED_ID, "removed-node", MemberStatus.REMOVED),
    ],
)


def test_sender_id_for_discord_author_when_active_member_then_returns_node_label() -> None:
    # Given / When
    sender_id = _ROSTER.sender_id_for_discord_author(_ACTIVE_ID)

    # Then
    assert sender_id == "active-node"


def test_sender_id_for_discord_author_when_admin_then_returns_publisher_principal() -> None:
    # Given / When
    sender_id = _ROSTER.sender_id_for_discord_author(_ADMIN_ID)

    # Then
    assert sender_id == "publisher-test-admin@autophagy"


@pytest.mark.parametrize("author_id", [_REMOVED_ID, "100000000000000099"])
def test_sender_id_for_discord_author_when_not_active_principal_then_returns_none(
    author_id: str,
) -> None:
    # Given / When
    sender_id = _ROSTER.sender_id_for_discord_author(author_id)

    # Then
    assert sender_id is None
