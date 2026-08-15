"""Hermes lifecycle-hook handler that reports Interop Protocol v0 state changes."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from automation.interop.discord_transport import DiscordTransport
from automation.interop.report import ReportStatus, TaskReport, format_report
from automation.repair.repair_reporter import record_lifecycle_failure


KST: Final = ZoneInfo("Asia/Seoul")


async def handle(event_type: str, context: dict[str, str]) -> None:
    """Send one strictly formatted task lifecycle report to ``#agents-log``."""
    record_lifecycle_failure(event_type, context)
    config = _load_config()
    status = _status_for(event_type, context)
    report = TaskReport(
        agent_id=config["agent_id"],
        task_id=context.get("task_id", context.get("session_id", "hermes-lifecycle")),
        status=status,
        summary=f"Hermes {event_type}",
        links=(),
        timestamp=datetime.now(KST),
    )
    token = os.environ["DISCORD_BOT_TOKEN"]
    DiscordTransport(token=token, channel_id=config["agents_log_channel_id"]).send(format_report(report))


def _load_config() -> dict[str, str]:
    config_path = Path(os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")).expanduser()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("interop config must be a JSON object")
    agent_id = payload.get("agent_id")
    channel_id = payload.get("agents_log_channel_id")
    if not isinstance(agent_id, str) or not isinstance(channel_id, str):
        raise ValueError("interop config missing required string identifiers")
    return {"agent_id": agent_id, "agents_log_channel_id": channel_id}


def _status_for(event_type: str, context: dict[str, str]) -> ReportStatus:
    if context.get("status") == "blocked" or context.get("error"):
        return ReportStatus.BLOCKED
    if event_type == "agent:start":
        return ReportStatus.START
    return ReportStatus.DONE
