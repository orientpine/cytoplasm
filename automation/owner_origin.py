"""The owner's own recent Discord message as the basis for an action taken without a card.

One implementation for the YooDori gate's owner-command jobs and the cardless release (owner
2026-11-02 Q447). It imports nothing from automation at module level, so the deploy gate can
stage it alone and the gate's standalone libexec copy can import it lazily.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

DISCORD_EPOCH_MS = 1420070400000
ORIGIN_MAX_AGE_S = 24 * 3600


class OriginRefused(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Origin:
    channel_id: str
    message_id: str
    link: str


Fetch = Callable[[str, str], dict]


def _discord_get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        f"https://discord.com/api/v10{path}",
        headers={"Authorization": f"Bot {token}", "User-Agent": "yoodori-gate (cytoplasm)"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("unexpected Discord response")
    return value


def verify_owner_origin(
    channel_id: str, message_id: str, owner_id: str, token: str, *,
    now: float | None = None, fetch: Fetch = _discord_get,
) -> Origin:
    """The origin must be the owner's own (non-bot) message from the last 24 hours."""
    if not token:
        raise OriginRefused("origin check needs the node Discord credential")
    sent = ((int(message_id) >> 22) + DISCORD_EPOCH_MS) / 1000
    if (now if now is not None else time.time()) - sent > ORIGIN_MAX_AGE_S:
        raise OriginRefused("origin message is older than 24 hours; ask the owner again")
    try:
        message = fetch(f"/channels/{channel_id}/messages/{message_id}", token)
        channel = fetch(f"/channels/{channel_id}", token)
    except (OSError, ValueError, urllib.error.URLError):
        raise OriginRefused("origin message could not be read; nothing was run") from None
    author = message.get("author")
    if (not isinstance(author, dict) or str(author.get("id")) != owner_id
            or author.get("bot") or str(message.get("id")) != message_id):
        raise OriginRefused("origin message is not the owner's own message")
    guild = channel.get("guild_id")
    from automation.interop.owner_message import discord_link

    if channel.get("type") == 1 and not guild:
        link = discord_link(space="dm", channel_id=channel_id, message_id=message_id)
    elif isinstance(guild, str) and guild.isdigit():
        link = discord_link(space="guild", guild_id=guild, channel_id=channel_id, message_id=message_id)
    else:
        raise OriginRefused("origin channel could not be placed")
    if not link.url:
        raise OriginRefused("origin message link could not be built")
    return Origin(channel_id, message_id, link.url)
