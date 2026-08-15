from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import yaml

from automation.group_roster import (
    MemberStatus,
    RosterError,
    YamlMapping,
    YamlValue,
    load_roster,
)

_VALID_PUBLIC_KEY = " ".join(
    (
        "ssh-ed25519",
        "AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g",
        "roster-example-admin",
    )
)


def _valid_payload() -> YamlMapping:
    return {
        "schema": 1,
        "group_id": "example-lab",
        "admin": {
            "name": "Example Admin",
            "discord_user_id": "1001",
            "publisher_principal": "publisher-example-admin@autophagy",
            "signing_public_key": _VALID_PUBLIC_KEY,
        },
        "members": [
            {
                "name": "Example Member One",
                "discord_user_id": "1002",
                "node_label": "member-one-node",
                "status": "active",
            },
            {
                "name": "Example Member Two",
                "discord_user_id": "1003",
                "node_label": "member-two-node",
                "status": "removed",
            },
        ],
    }


def _write_payload(tmp_path: Path, payload: YamlValue) -> Path:
    path = tmp_path / "roster.yaml"
    _ = path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _admin(payload: YamlMapping) -> YamlMapping:
    admin = payload["admin"]
    assert isinstance(admin, dict)
    return admin


def _members(payload: YamlMapping) -> list[YamlMapping]:
    members = payload["members"]
    assert isinstance(members, list)
    typed_members: list[YamlMapping] = []
    for member in members:
        assert isinstance(member, dict)
        typed_members.append(member)
    return typed_members


def test_load_roster_when_valid_then_returns_typed_model(tmp_path: Path) -> None:
    # Given
    path = _write_payload(tmp_path, _valid_payload())

    # When
    roster = load_roster(path)

    # Then
    assert roster.schema == 1
    assert roster.group_id == "example-lab"
    assert roster.admin.name == "Example Admin"
    assert roster.members[0].status is MemberStatus.ACTIVE
    assert roster.members[1].status is MemberStatus.REMOVED
    assert roster.update_channel is None
    assert roster.announce_channel_id is None


def test_load_roster_when_update_channel_present_then_preserves_it(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    payload["update_channel"] = "https://updates.example.invalid/autophagy.git"

    # When
    roster = load_roster(_write_payload(tmp_path, payload))

    # Then
    assert roster.update_channel == "https://updates.example.invalid/autophagy.git"


def test_load_roster_when_announce_channel_present_then_preserves_it(tmp_path: Path) -> None:
    # Given: a group that publishes release announcements into one Discord channel.
    payload = _valid_payload()
    payload["announce_channel_id"] = "1004"

    # When
    roster = load_roster(_write_payload(tmp_path, payload))

    # Then
    assert roster.announce_channel_id == "1004"


@pytest.mark.parametrize("value", ["", "   ", "#releases", "100 4", 1004])
def test_load_roster_rejects_non_numeric_announce_channel(
    tmp_path: Path, value: object
) -> None:
    # Given: an announce channel that is not a Discord snowflake string.
    payload = _valid_payload()
    payload["announce_channel_id"] = cast("YamlValue", value)

    # Then: the roster is refused rather than announcing into an unknown place.
    with pytest.raises(RosterError, match=r"announce_channel_id must be"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_missing_admin(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    del payload["admin"]

    # Then
    with pytest.raises(RosterError, match=r"missing required.*admin"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_null_admin(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    payload["admin"] = None

    # Then
    with pytest.raises(RosterError, match=r"admin.*mapping.*exactly one"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_multiple_admin_shape(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    payload["admin"] = [_admin(payload), _admin(payload)]

    # Then
    with pytest.raises(RosterError, match=r"admin.*mapping.*exactly one"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_duplicate_admin_member_discord_id(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    _members(payload)[0]["discord_user_id"] = _admin(payload)["discord_user_id"]

    # Then
    with pytest.raises(RosterError, match=r"duplicate discord_user_id.*1001"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_duplicate_member_discord_ids(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    _members(payload)[1]["discord_user_id"] = "1002"

    # Then
    with pytest.raises(RosterError, match=r"duplicate discord_user_id.*1002"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_malformed_publisher_principal(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    _admin(payload)["publisher_principal"] = "publisher@example.invalid"

    # Then
    with pytest.raises(RosterError, match=r"publisher_principal.*publisher-.*@autophagy"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_malformed_signing_public_key(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    _admin(payload)["signing_public_key"] = "ssh-ed25519 not-base64!"

    # Then
    with pytest.raises(RosterError, match=r"signing_public_key.*OpenSSH"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_unrecognized_signing_key_algorithm(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    _admin(payload)["signing_public_key"] = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC0"

    # Then
    with pytest.raises(RosterError, match=r"signing_public_key.*ssh-ed25519"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_group_id_list(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    payload["group_id"] = ["example-lab", "other-lab"]

    # Then
    with pytest.raises(RosterError, match=r"group_id.*non-empty string"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_unknown_member_status(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    _members(payload)[0]["status"] = "pending"

    # Then
    with pytest.raises(RosterError, match=r"status.*active.*removed"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_malformed_yaml(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "roster.yaml"
    _ = path.write_text("schema: 1\nadmin: [\n", encoding="utf-8")

    # Then
    with pytest.raises(RosterError, match=r"valid YAML"):
        _ = load_roster(path)


def test_load_roster_rejects_missing_required_field(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    del payload["group_id"]

    # Then
    with pytest.raises(RosterError, match=r"missing required.*group_id"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_unknown_roster_field(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    payload["extra"] = True

    # Then
    with pytest.raises(RosterError, match=r"unknown roster fields.*extra"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_unknown_admin_field(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    _admin(payload)["role"] = "owner"

    # Then
    with pytest.raises(RosterError, match=r"unknown admin fields.*role"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_unknown_member_field(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    _members(payload)[0]["role"] = "researcher"

    # Then
    with pytest.raises(RosterError, match=r"unknown members\[0\] fields.*role"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_unsupported_schema(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    payload["schema"] = 2

    # Then
    with pytest.raises(RosterError, match=r"schema.*int 1"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_non_numeric_discord_user_id(tmp_path: Path) -> None:
    # Given
    payload = _valid_payload()
    _members(payload)[0]["discord_user_id"] = "member-one"

    # Then
    with pytest.raises(RosterError, match=r"discord_user_id.*numeric string"):
        _ = load_roster(_write_payload(tmp_path, payload))


def test_load_roster_rejects_duplicate_yaml_key(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "roster.yaml"
    duplicate_key_yaml = "\n".join(
        (
            "schema: 1",
            "group_id: example-lab",
            "group_id: shadow-lab",
            "admin: {}",
            "members: []",
            "",
        )
    )
    _ = path.write_text(duplicate_key_yaml, encoding="utf-8")

    # Then
    with pytest.raises(RosterError, match=r"duplicate YAML key.*group_id"):
        _ = load_roster(path)


def test_load_roster_rejects_yaml_merge_key(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "roster.yaml"
    merge_key_yaml = "\n".join(
        (
            "schema: 1",
            "group_id: example-lab",
            "admin:",
            "  <<: &admin_fields",
            "    name: Example Admin",
            '    discord_user_id: "1001"',
            "    publisher_principal: publisher-example-admin@autophagy",
            f"    signing_public_key: {_VALID_PUBLIC_KEY}",
            "members: []",
            "",
        )
    )
    _ = path.write_text(merge_key_yaml, encoding="utf-8")

    # Then
    with pytest.raises(RosterError, match=r"YAML merge keys are not supported"):
        _ = load_roster(path)
