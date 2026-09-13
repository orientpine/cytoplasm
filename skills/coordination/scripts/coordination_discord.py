"""Discord wire adapter for coordination approval binding probes."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote

import coordinate_io as io
from coordination_binding import lifecycle
from coordination_pending import PendingConfirm

_TRANSPORT_ERRORS = (
    io.CoordinationError,
    URLError,
    OSError,
    json.JSONDecodeError,
    KeyError,
    TypeError,
)

@dataclass(frozen=True, slots=True)
class CoordinationDiscord:
    owner_id: str

    def message_content(self, entry: PendingConfirm) -> str | None:
        try:
            message = io.api(
                "GET", f"/channels/{entry.dm_channel_id}/messages/{entry.dm_message_id}"
            )
        except HTTPError as error:
            if error.code == 404:
                return None
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise lifecycle().ApprovalSurfaceError("confirmation DM response is invalid")
        return str(message["content"])

    def reaction_users(
        self, entry: PendingConfirm, emoji: str
    ) -> tuple[Mapping[str, str | bool], ...]:
        endpoint = (
            f"/channels/{entry.dm_channel_id}/messages/{entry.dm_message_id}"
            f"/reactions/{quote(emoji, safe='')}?limit=100"
        )
        try:
            users = io.api("GET", endpoint)
        except HTTPError as error:
            if error.code == 404:
                return ()
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        if not isinstance(users, list):
            raise lifecycle().ApprovalSurfaceError("reaction response is invalid")
        return tuple(user for user in users if isinstance(user, dict))

    def delete(self, entry: PendingConfirm) -> None:
        try:
            io.api("DELETE", f"/channels/{entry.dm_channel_id}/messages/{entry.dm_message_id}")
        except HTTPError as error:
            if error.code != 404:
                raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
