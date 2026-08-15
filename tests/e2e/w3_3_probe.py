#!/usr/bin/env python3
"""Masked Discord probes for the W3-3 coordination E2E (agent account).

Modes:
  dm-check <needle...>   exit 0 when one owner-DM message contains ALL needles
  team-after <iso-utc>   masked classification of #team messages after a time
Output masks every id to its 4-char suffix and never prints message bodies.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

API = "https://discord.com/api/v10"


def api(method: str, path: str, payload: dict | None = None):
    request = urllib.request.Request(
        API + path,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {os.environ['DISCORD_BOT_TOKEN']}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)",
        },
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def owner_id() -> str:
    config = Path("~/.hermes/interop/config.json").expanduser()
    return json.loads(config.read_text(encoding="utf-8"))["owner_id"]


def team_channel() -> str:
    guild = next(g for g in api("GET", "/users/@me/guilds") if g["name"] == "Autophagy Lab")
    return next(
        c["id"] for c in api("GET", f"/guilds/{guild['id']}/channels") if c["name"] == "team"
    )


def dm_check(needles: list[str]) -> int:
    channel = api("POST", "/users/@me/channels", {"recipient_id": owner_id()})["id"]
    for message in api("GET", f"/channels/{channel}/messages?limit=50"):
        content = str(message.get("content", ""))
        if all(needle in content for needle in needles):
            print(f"DM-FOUND id_suffix={message['id'][-4:]} len={len(content)}")
            return 0
    print("DM-MISSING")
    return 1


def team_after(since_iso: str) -> int:
    sys.path.insert(0, os.path.expanduser("~/.hermes/interop_runtime"))
    from automation.interop.delegation import parse_envelope

    since = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
    counts = {"envelope": 0, "notice": 0, "other": 0}
    for message in reversed(api("GET", f"/channels/{team_channel()}/messages?limit=30")):
        created = datetime.fromisoformat(str(message["timestamp"]))
        if created <= since:
            continue
        content = str(message.get("content", ""))
        if parse_envelope(content) is not None:
            kind = "envelope"
        elif content.startswith("📅 일정 확정"):
            kind = "notice"
        else:
            kind = "other"
        counts[kind] += 1
        author = message.get("author", {})
        print(
            f"TEAM msg_suffix={message['id'][-4:]} author_suffix={str(author.get('id', ''))[-4:]} "
            f"bot={author.get('bot', False)} kind={kind} len={len(content)}"
        )
    print(
        f"TEAM-AFTER envelopes={counts['envelope']} notices={counts['notice']} "
        f"others={counts['other']}"
    )
    return 0


def main() -> int:
    mode = sys.argv[1]
    if mode == "dm-check":
        return dm_check(sys.argv[2:])
    if mode == "team-after":
        return team_after(sys.argv[2])
    print("usage: w3_3_probe.py dm-check <needle...> | team-after <iso-utc>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
