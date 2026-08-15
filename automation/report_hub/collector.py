"""Report hub collector: poll #agents-log through the hub bot and load SQLite.

Runs as the ops account. On every start it re-fetches Discord history after the
persisted watermark (full history on first run), so restarts backfill any
messages missed while the collector was down. Non-conformant messages are
never written to the main table; they are appended to a quarantine JSONL log.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Final

from automation.report_hub.classify import AcceptedReport, classify_message
from automation.report_hub.registry import PeerRegistry, load_registry
from automation.report_hub.store import ReportRow, ReportStore, utc_now_iso

DISCORD_API: Final = "https://discord.com/api/v10"
USER_AGENT: Final = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
PAGE_LIMIT: Final = 100

logger = logging.getLogger("report_hub.collector")


class CollectorConfigError(RuntimeError):
    """The environment does not provide a usable collector configuration."""


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CollectorConfigError(f"missing required environment variable {name}")
    return value


class DiscordReader:
    """Minimal authenticated read-only Discord REST client for the hub bot."""

    def __init__(self, token: str) -> None:
        self._headers = {
            "Authorization": f"Bot {token}",
            "User-Agent": USER_AGENT,
        }

    def _get(self, path: str) -> Any:
        request = urllib.request.Request(f"{DISCORD_API}{path}", headers=self._headers)
        while True:
            try:
                with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                if error.code != 429:
                    raise
                retry_after = error.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else 1.0)

    def resolve_channel_id(self, guild_id: str, channel_name: str) -> str:
        channels = self._get(f"/guilds/{guild_id}/channels")
        for channel in channels:
            if channel.get("name") == channel_name:
                return str(channel["id"])
        raise CollectorConfigError(f"channel #{channel_name} not found in guild")

    def messages_after(self, channel_id: str, after: str | None) -> list[dict[str, Any]]:
        """One ascending page of up to PAGE_LIMIT messages newer than `after`."""
        anchor = after if after is not None else "0"
        page = self._get(f"/channels/{channel_id}/messages?after={anchor}&limit={PAGE_LIMIT}")
        return sorted(page, key=lambda message: int(message["id"]))


def _quarantine(log_path: Path, message: dict[str, Any], reason: str) -> None:
    content = str(message.get("content", ""))
    record = {
        "quarantined_at": utc_now_iso(),
        "message_id": str(message["id"]),
        "author_id": str(message["author"]["id"]),
        "author_name": str(message["author"].get("username", "")),
        "reason": reason,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content_excerpt": content[:120],
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _ingest_message(
    store: ReportStore,
    registry: PeerRegistry,
    quarantine_log: Path,
    channel_id: str,
    message: dict[str, Any],
) -> str:
    """Classify one Discord message and persist the outcome. Returns the verdict."""
    author = message["author"]
    outcome = classify_message(
        content=str(message.get("content", "")),
        author_bot_user_id=str(author["id"]),
        registry=registry,
    )
    if isinstance(outcome, AcceptedReport):
        report = outcome.report
        store.upsert_report(
            ReportRow(
                message_id=str(message["id"]),
                channel_id=channel_id,
                author_id=str(author["id"]),
                author_name=str(author.get("username", "")),
                agent_id=report.agent_id,
                task_id=report.task_id,
                status=report.status.value,
                summary=report.summary,
                links=report.links,
                report_timestamp=report.timestamp.isoformat(),
                discord_timestamp=str(message.get("timestamp", "")),
                registered=outcome.registered,
                registration_note=outcome.registration_note,
                collected_at=utc_now_iso(),
            )
        )
        return outcome.registration_note
    _quarantine(quarantine_log, message, outcome.reason)
    return "quarantined"


def collect_new_messages(
    reader: DiscordReader,
    store: ReportStore,
    registry: PeerRegistry,
    quarantine_log: Path,
    channel_id: str,
) -> int:
    """Drain every message newer than the watermark; advance it per page."""
    processed = 0
    while True:
        page = reader.messages_after(channel_id, store.watermark())
        if not page:
            return processed
        for message in page:
            verdict = _ingest_message(store, registry, quarantine_log, channel_id, message)
            logger.info("message %s -> %s", message["id"], verdict)
            processed += 1
        store.set_watermark(str(page[-1]["id"]))
        if len(page) < PAGE_LIMIT:
            return processed


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    token = _require_env("DISCORD_BOT_TOKEN")
    guild_id = _require_env("REPORT_HUB_GUILD_ID")
    channel_name = os.environ.get("REPORT_HUB_CHANNEL_NAME", "agents-log")
    database_path = Path(_require_env("REPORT_HUB_DB"))
    quarantine_log = Path(_require_env("REPORT_HUB_QUARANTINE_LOG"))
    peers_file = Path(_require_env("REPORT_HUB_PEERS_FILE"))
    poll_seconds = float(os.environ.get("REPORT_HUB_POLL_SECONDS", "10"))

    registry = load_registry(peers_file)
    store = ReportStore(database_path)
    reader = DiscordReader(token)
    channel_id = reader.resolve_channel_id(guild_id, channel_name)
    logger.info("collector online; polling #%s every %.0fs", channel_name, poll_seconds)

    while True:
        try:
            collect_new_messages(reader, store, registry, quarantine_log, channel_id)
        except urllib.error.URLError as error:
            logger.warning("discord fetch failed, retrying next tick: %s", error)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
