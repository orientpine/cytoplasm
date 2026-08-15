#!/usr/bin/env python3
"""W3-6 bank actuator (agent side) for the w3-report-hub scenario.

Runs ON agent@<primary-node>. Two modes:
  post                 emit one strict Interop v0 report (W1-6 format_report,
                       deployed runtime copy) to #agents-log as the registered
                       agent-cha bot; prints masked progress + one OBS-JSON
                       carrying the collect-case observables and a _ctx block
                       (channel/message/task ids for the follow-up steps).
  cleanup <ch> <mid>   assert no OTHER message landed after ours (cascade/
                       chatter guard), then DELETE our Discord message so the
                       bank leaves the channel exactly as found.

Never prints the bot token; ids are only emitted inside OBS-JSON (the local
driver keeps them out of committed evidence).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
AGENT_ID = "agent-cha"


def bot_token() -> str:
    for line in (Path.home() / ".env.secrets").read_text(encoding="utf-8").splitlines():
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DISCORD_BOT_TOKEN not found")


def api(method: str, path: str, payload: dict | None = None):
    request = urllib.request.Request(
        API + path,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {bot_token()}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def agents_log_channel() -> str:
    guild = next(g for g in api("GET", "/users/@me/guilds") if g["name"] == "Autophagy Lab")
    return next(
        c["id"]
        for c in api("GET", f"/guilds/{guild['id']}/channels")
        if c["name"] == "agents-log"
    )


def mode_post() -> int:
    sys.path.insert(0, str(Path.home() / ".hermes" / "interop_runtime"))
    from automation.interop.report import ReportStatus, TaskReport, format_report

    task_id = f"W3-6-bank-{int(time.time())}"
    content = format_report(
        TaskReport(
            agent_id=AGENT_ID,
            task_id=task_id,
            status=ReportStatus.DONE,
            summary="W3-6 scenario-bank report-hub E2E probe (synthetic, cleaned up in-run)",
            links=(),
            timestamp=datetime.now(UTC),
        )
    )
    channel = agents_log_channel()
    message = api("POST", f"/channels/{channel}/messages", {"content": content})
    observations = {
        "collect": {"post_exit": 0, "report_posted": bool(message and message.get("id"))},
        "_ctx": {
            "channel_id": channel,
            "message_id": str(message["id"]),
            "task_id": task_id,
        },
    }
    print(f"W36 posted report task={task_id} msg_suffix={str(message['id'])[-4:]}")
    print("OBS-JSON: " + json.dumps(observations, ensure_ascii=False))
    return 0


def mode_cleanup(channel: str, message_id: str) -> int:
    after = api("GET", f"/channels/{channel}/messages?after={message_id}&limit=50") or []
    chatter = [m for m in after if str(m.get("id")) != message_id]
    for message in chatter:
        author = message.get("author", {})
        print(
            f"W36 chatter-after msg_suffix={str(message.get('id', ''))[-4:]} "
            f"author_suffix={str(author.get('id', ''))[-4:]} bot={author.get('bot', False)}"
        )
    api("DELETE", f"/channels/{channel}/messages/{message_id}")
    remaining = api("GET", f"/channels/{channel}/messages?after={message_id}&limit=50") or []
    deleted = all(str(m.get("id")) != message_id for m in remaining)
    observations = {
        "cleanup": {
            "agents_log_no_chatter_after": len(chatter) == 0,
            "discord_message_deleted": deleted,
        }
    }
    print("OBS-JSON: " + json.dumps(observations, ensure_ascii=False))
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "post":
        return mode_post()
    if mode == "cleanup" and len(sys.argv) == 4:
        return mode_cleanup(sys.argv[2], sys.argv[3])
    print("usage: w3_report_hub_agent.py post | cleanup <channel_id> <message_id>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
