"""Fail-closed conversion of untrusted YAML values into a typed roster."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal, TypeAlias

from ..typing_compat import override

from .schema import SCHEMA_VERSION, MemberStatus, Roster, RosterAdmin, RosterMember


@dataclass(frozen=True, slots=True)
class RosterError(ValueError):
    """The roster document failed schema validation."""

    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class _FieldSpec:
    required: frozenset[str]
    optional: frozenset[str]
    shape_error: str


_ROSTER_FIELDS: Final = _FieldSpec(
    required=frozenset({"schema", "group_id", "admin", "members"}),
    optional=frozenset({"update_channel", "announce_channel_id", "revision"}),
    shape_error="roster must be a mapping",
)
_ADMIN_FIELDS: Final = _FieldSpec(
    required=frozenset(
        {"name", "discord_user_id", "publisher_principal", "signing_public_key"}
    ),
    optional=frozenset(),
    shape_error="admin must be a mapping describing exactly one administrator",
)
_MEMBER_FIELDS: Final = _FieldSpec(
    required=frozenset({"name", "discord_user_id", "node_label", "status"}),
    optional=frozenset(),
    shape_error="member must be a mapping",
)
_PUBLISHER_PRINCIPAL: Final = re.compile(
    r"\Apublisher-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?@autophagy\Z"
)
_SSH_ED25519: Final = "ssh-ed25519"
_SSH_ED25519_WIRE_PREFIX: Final = (
    len(_SSH_ED25519).to_bytes(4, "big")
    + _SSH_ED25519.encode("ascii")
    + (32).to_bytes(4, "big")
)

YamlScalar: TypeAlias = str | int | float | bool | None | bytes | date | datetime
YamlKey: TypeAlias = YamlScalar | tuple["YamlKey", ...] | frozenset["YamlKey"]
YamlValue: TypeAlias = (
    YamlKey | list["YamlValue"] | dict[YamlKey, "YamlValue"] | set[YamlKey]
)
YamlMapping: TypeAlias = dict[YamlKey, YamlValue]
_Mapping: TypeAlias = dict[str, YamlValue]


def _mapping(context: str, value: YamlValue, spec: _FieldSpec) -> _Mapping:
    if not isinstance(value, dict):
        raise RosterError(spec.shape_error)
    node: _Mapping = {}
    for key, entry in value.items():
        if not isinstance(key, str):
            raise RosterError(f"{context} field names must be strings")
        node[key] = entry
    unknown = sorted(set(node) - spec.required - spec.optional)
    if unknown:
        raise RosterError(f"unknown {context} fields: {', '.join(unknown)}")
    missing = sorted(spec.required - set(node))
    if missing:
        raise RosterError(f"missing required {context} fields: {', '.join(missing)}")
    return node


def _non_empty_string(field: str, value: YamlValue) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RosterError(f"{field} must be a non-empty string")
    return value


def _discord_user_id(field: str, value: YamlValue) -> str:
    user_id = _non_empty_string(field, value)
    if not user_id.isascii() or not user_id.isdecimal():
        raise RosterError(f"{field} must be a numeric string")
    return user_id


def _discord_channel_id(field: str, value: YamlValue) -> str:
    channel_id = _non_empty_string(field, value)
    if not channel_id.isascii() or not channel_id.isdecimal():
        raise RosterError(f"{field} must be a numeric string")
    return channel_id


def _publisher_principal(value: YamlValue) -> str:
    principal = _non_empty_string("admin.publisher_principal", value)
    if _PUBLISHER_PRINCIPAL.fullmatch(principal) is None:
        raise RosterError(
            "admin.publisher_principal must match publisher-<name>@autophagy"
        )
    return principal


def _signing_public_key(value: YamlValue) -> str:
    public_key = _non_empty_string("admin.signing_public_key", value)
    if "\n" in public_key or "\r" in public_key:
        raise RosterError("admin.signing_public_key must be one OpenSSH public-key line")
    parts = public_key.split(maxsplit=2)
    if len(parts) < 2:
        raise RosterError("admin.signing_public_key must be a valid OpenSSH public key")
    if parts[0] != _SSH_ED25519:
        raise RosterError("admin.signing_public_key must use ssh-ed25519")
    try:
        wire_key = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError) as error:
        raise RosterError(
            "admin.signing_public_key must be a valid OpenSSH public key"
        ) from error
    if (
        not wire_key.startswith(_SSH_ED25519_WIRE_PREFIX)
        or len(wire_key) != len(_SSH_ED25519_WIRE_PREFIX) + 32
    ):
        raise RosterError("admin.signing_public_key must be a valid OpenSSH public key")
    return public_key


def _admin(value: YamlValue) -> RosterAdmin:
    node = _mapping("admin", value, _ADMIN_FIELDS)
    return RosterAdmin(
        name=_non_empty_string("admin.name", node["name"]),
        discord_user_id=_discord_user_id(
            "admin.discord_user_id", node["discord_user_id"]
        ),
        publisher_principal=_publisher_principal(node["publisher_principal"]),
        signing_public_key=_signing_public_key(node["signing_public_key"]),
    )


def _member(value: YamlValue, index: int) -> RosterMember:
    context = f"members[{index}]"
    spec = _FieldSpec(
        required=_MEMBER_FIELDS.required,
        optional=_MEMBER_FIELDS.optional,
        shape_error=f"{context} must be a mapping",
    )
    node = _mapping(context, value, spec)
    raw_status = _non_empty_string(f"{context}.status", node["status"])
    try:
        status = MemberStatus(raw_status)
    except ValueError as error:
        raise RosterError(
            f"{context}.status must be one of: active, removed"
        ) from error
    return RosterMember(
        name=_non_empty_string(f"{context}.name", node["name"]),
        discord_user_id=_discord_user_id(
            f"{context}.discord_user_id", node["discord_user_id"]
        ),
        node_label=_non_empty_string(f"{context}.node_label", node["node_label"]),
        status=status,
    )


def _members(value: YamlValue) -> list[RosterMember]:
    if not isinstance(value, list):
        raise RosterError("members must be a list")
    return [_member(entry, index) for index, entry in enumerate(value)]


def _schema(value: YamlValue) -> Literal[1]:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value != SCHEMA_VERSION
    ):
        raise RosterError(f"schema must be the int {SCHEMA_VERSION}")
    return SCHEMA_VERSION


def _update_channel(node: _Mapping) -> str | None:
    if "update_channel" not in node:
        return None
    return _non_empty_string("update_channel", node["update_channel"])


def _announce_channel_id(node: _Mapping) -> str | None:
    if "announce_channel_id" not in node:
        return None
    return _discord_channel_id("announce_channel_id", node["announce_channel_id"])


def _revision(node: _Mapping) -> int | None:
    """Parse the optional monotonic publication counter (rollback ordering)."""
    if "revision" not in node:
        return None
    value = node["revision"]
    # `bool` is an `int` subclass, and YAML renders `true` as one. A roster whose
    # order is `True` would compare as 1 against every real revision.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RosterError("revision must be a positive integer")
    return value


def validate_roster(raw: YamlValue) -> Roster:
    """Convert one untrusted YAML value into a fully validated roster."""
    node = _mapping("roster", raw, _ROSTER_FIELDS)
    admin = _admin(node["admin"])
    members = _members(node["members"])
    seen_ids = {admin.discord_user_id}
    for member in members:
        if member.discord_user_id in seen_ids:
            raise RosterError(
                f"duplicate discord_user_id across admin and members: {member.discord_user_id}"
            )
        seen_ids.add(member.discord_user_id)
    return Roster(
        schema=_schema(node["schema"]),
        group_id=_non_empty_string("group_id", node["group_id"]),
        admin=admin,
        members=members,
        update_channel=_update_channel(node),
        announce_channel_id=_announce_channel_id(node),
        revision=_revision(node),
    )
