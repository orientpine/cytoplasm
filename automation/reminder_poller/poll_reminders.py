#!/usr/bin/env python3
"""Reminder poller (W3-2) — Hermes cron job, no_agent script mode, every 5 min.

Reads cha's calendar through the W3-1 read-only list path (`gws calendar
events list`) and DMs cha for any event starting 55-65 minutes out; reads
~/state/milestones.yaml (W2-3) and DMs cha at D-3/D-1/D-day. Both legs are
idempotent through the SQLite claim-before-send store (reminder_store), so a
duplicate poller invocation sends zero duplicates.

The calendar is NEVER mutated here (list only). The DM is an internal
notification to cha's own DM — not an external effect, so no approval gate.

TZ: the host is UTC; all arithmetic is timezone-aware and milestone "today"
is the KST calendar date. The Hermes cron schedule itself follows the agent
config `timezone: Asia/Seoul` (W1-7 mechanism).

Deployed copy lives at ~/.hermes/scripts/poll_reminders.py (Hermes cron
sandbox rule) and imports the runtime package copy under
~/.hermes/reminder_poller_runtime/. Force-run:

    hermes cron run <job-id>            # or directly:
    sudo -u agent -H python3 ~/.hermes/scripts/poll_reminders.py

no_agent semantics: empty stdout + exit 0 on success (silent tick); on
failure prints one masked line and exits 1 so the scheduler records an alert.

Env hooks (never set on the production cron path):
  REMINDER_DB               SQLite path (default ~/state/reminders.db)
  REMINDER_MILESTONES_FILE  milestones.yaml path (default ~/state/milestones.yaml)
  REMINDER_EVENTS_FILE      read events JSON from a file instead of gws
  REMINDER_CALENDAR         calendarId (default primary)
  REMINDER_GWS_BIN          gws binary override
  REMINDER_NOW              fixed ISO-8601 now (deterministic tests)
  REMINDER_DRY_RUN=1        print composed reminders instead of sending DMs
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from automation.reminder_poller import poller_core, reminder_store
except ImportError:
    # Deployed layout: ~/.hermes/scripts/ + FLAT modules in the runtime dir.
    # Flat on purpose: the Hermes cron sandbox inherits
    # PYTHONPATH=~/.hermes/interop_runtime (W1-6 hooks), whose `automation`
    # package wins the import and gets cached in sys.modules — a nested
    # automation/reminder_poller runtime copy is unreachable there.
    sys.path.insert(0, str(Path.home() / ".hermes" / "reminder_poller_runtime"))
    import poller_core  # pyright: ignore[reportMissingImports] # noqa: F401
    import reminder_store  # pyright: ignore[reportMissingImports] # noqa: F401

DISCORD_API = "https://discord.com/api/v10"
ENV_SECRETS = Path.home() / ".env.secrets"
INTEROP_CONFIG = Path.home() / ".hermes" / "interop" / "config.json"
GWS_TIMEOUT_S = 120
LOOKAHEAD = timedelta(minutes=90)


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def poll_now() -> datetime:
    override = os.environ.get("REMINDER_NOW", "")
    if override:
        return datetime.fromisoformat(override).astimezone(poller_core.KST)
    return datetime.now(poller_core.KST)


def gws_bin() -> str:
    override = os.environ.get("REMINDER_GWS_BIN", "")
    if override:
        return override
    import shutil

    found = shutil.which("gws") or os.path.expanduser("~/.local/bin/gws")
    if not Path(found).exists():
        raise RuntimeError("gws CLI not found (set REMINDER_GWS_BIN)")
    return found


def fetch_events(now: datetime) -> list[poller_core.CalendarEvent]:
    override = os.environ.get("REMINDER_EVENTS_FILE", "")
    if override:
        return poller_core.parse_events(Path(override).read_text(encoding="utf-8"))
    params = {
        "calendarId": os.environ.get("REMINDER_CALENDAR", "primary"),
        "maxResults": 50,
        "orderBy": "startTime",
        "singleEvents": True,
        "timeMax": (now + LOOKAHEAD).isoformat(),
        "timeMin": now.isoformat(),
    }
    result = subprocess.run(  # noqa: S603 — frozen read-only argv
        [gws_bin(), "calendar", "events", "list", "--params",
         json.dumps(params, ensure_ascii=False)],
        capture_output=True, text=True, timeout=GWS_TIMEOUT_S, check=False,
        cwd=str(Path.home()),  # gws writes empty responses to cwd files (W3-1 gotcha)
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gws list failed rc={result.returncode}: {result.stderr.strip()[:200]}"
        )
    return poller_core.parse_events(result.stdout)


def read_milestones() -> list[dict[str, str]]:
    path = _env_path("REMINDER_MILESTONES_FILE", "~/state/milestones.yaml")
    if not path.exists():
        return []
    return poller_core.parse_milestones(path.read_text(encoding="utf-8"))


def bot_token() -> str:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if token:
        return token
    for line in ENV_SECRETS.read_text(encoding="utf-8").splitlines():
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DISCORD_BOT_TOKEN not available")


def _discord_post(token: str, path: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        DISCORD_API + path,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)",
        },
        method="POST",
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 — fixed https host
        raw = response.read().decode()
    parsed: object = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("discord response is not a JSON object")
    return parsed


class DmSender:
    """Owner-DM transport: channel created once, then one POST per reminder."""

    def __init__(self) -> None:
        self._channel_id = ""

    def send(self, body: str) -> None:
        if os.environ.get("REMINDER_DRY_RUN", "") == "1":
            print(f"DRY-RUN {body}")
            return
        token = bot_token()
        if not self._channel_id:
            config: object = json.loads(INTEROP_CONFIG.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise RuntimeError("interop config is not a JSON object")
            channel = _discord_post(
                token, "/users/@me/channels", {"recipient_id": str(config["owner_id"])}
            )
            self._channel_id = str(channel["id"])
        _discord_post(token, f"/channels/{self._channel_id}/messages", {"content": body})


def _deliver(db: Path, sender: DmSender, kind: str, key: str, body: str) -> None:
    """Claim-before-send; a failed send releases the claim for retry."""
    if not reminder_store.claim(db, kind, key):
        return
    try:
        sender.send(body)
    except BaseException:
        reminder_store.release(db, kind, key)
        raise


def main() -> int:
    now = poll_now()
    db = _env_path("REMINDER_DB", "~/state/reminders.db")
    sender = DmSender()
    for event in fetch_events(now):
        if not poller_core.in_reminder_window(event.start, now):
            continue
        _deliver(
            db, sender, "event", poller_core.event_key(event),
            poller_core.compose_event_reminder(event, now),
        )
    today = now.date()
    for entry in read_milestones():
        offset = poller_core.milestone_offset(entry.get("deadline", ""), today)
        if offset is None:
            continue
        _deliver(
            db, sender, "milestone", poller_core.milestone_key(entry, offset),
            poller_core.compose_milestone_reminder(entry, offset),
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — cron alert path: one masked line
        print(f"reminder-poller error: {poller_core.redact(str(error))[:300]}")
        sys.exit(1)
