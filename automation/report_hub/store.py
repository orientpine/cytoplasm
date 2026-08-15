"""SQLite persistence for collected reports, watermark state, and query views."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
  message_id        TEXT PRIMARY KEY,
  channel_id        TEXT NOT NULL,
  author_id         TEXT NOT NULL,
  author_name       TEXT NOT NULL,
  agent_id          TEXT NOT NULL,
  task_id           TEXT NOT NULL,
  status            TEXT NOT NULL,
  summary           TEXT NOT NULL,
  links             TEXT NOT NULL,
  report_timestamp  TEXT NOT NULL,
  discord_timestamp TEXT NOT NULL,
  registered        INTEGER NOT NULL,
  registration_note TEXT NOT NULL,
  collected_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_agent ON reports (agent_id);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports (status);
CREATE TABLE IF NOT EXISTS collector_state (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

_WATERMARK_KEY = "agents_log_watermark"


@dataclass(frozen=True, slots=True)
class ReportRow:
    """One accepted report as persisted in the main table."""

    message_id: str
    channel_id: str
    author_id: str
    author_name: str
    agent_id: str
    task_id: str
    status: str
    summary: str
    links: tuple[str, ...]
    report_timestamp: str
    discord_timestamp: str
    registered: bool
    registration_note: str
    collected_at: str


def _row_from_record(record: sqlite3.Row) -> ReportRow:
    return ReportRow(
        message_id=record["message_id"],
        channel_id=record["channel_id"],
        author_id=record["author_id"],
        author_name=record["author_name"],
        agent_id=record["agent_id"],
        task_id=record["task_id"],
        status=record["status"],
        summary=record["summary"],
        links=tuple(json.loads(record["links"])),
        report_timestamp=record["report_timestamp"],
        discord_timestamp=record["discord_timestamp"],
        registered=bool(record["registered"]),
        registration_note=record["registration_note"],
        collected_at=record["collected_at"],
    )


class ReportStore:
    """Writer-side store used by the collector."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def upsert_report(self, row: ReportRow) -> None:
        """Insert or replace by Discord message id, so re-collection is idempotent."""
        self._connection.execute(
            "INSERT OR REPLACE INTO reports VALUES "
            "(:message_id, :channel_id, :author_id, :author_name, :agent_id, :task_id,"
            " :status, :summary, :links, :report_timestamp, :discord_timestamp,"
            " :registered, :registration_note, :collected_at)",
            {
                "message_id": row.message_id,
                "channel_id": row.channel_id,
                "author_id": row.author_id,
                "author_name": row.author_name,
                "agent_id": row.agent_id,
                "task_id": row.task_id,
                "status": row.status,
                "summary": row.summary,
                "links": json.dumps(list(row.links), ensure_ascii=False),
                "report_timestamp": row.report_timestamp,
                "discord_timestamp": row.discord_timestamp,
                "registered": int(row.registered),
                "registration_note": row.registration_note,
                "collected_at": row.collected_at,
            },
        )
        self._connection.commit()

    def watermark(self) -> str | None:
        """Return the newest processed Discord message id, if any."""
        record = self._connection.execute(
            "SELECT value FROM collector_state WHERE key = ?", (_WATERMARK_KEY,)
        ).fetchone()
        return None if record is None else str(record["value"])

    def set_watermark(self, message_id: str) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO collector_state (key, value) VALUES (?, ?)",
            (_WATERMARK_KEY, message_id),
        )
        self._connection.commit()


def utc_now_iso() -> str:
    """A second-resolution UTC timestamp for collected_at columns."""
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


class ReportQuery:
    """Read-only view used by the dashboard."""

    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self._connection.row_factory = sqlite3.Row

    def close(self) -> None:
        self._connection.close()

    def reports(self, agent_id: str | None = None, status: str | None = None) -> list[ReportRow]:
        """Timeline-ordered reports, optionally filtered by agent and status."""
        clauses: list[str] = []
        parameters: list[str] = []
        if agent_id:
            clauses.append("agent_id = ?")
            parameters.append(agent_id)
        if status:
            clauses.append("status = ?")
            parameters.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        records = self._connection.execute(
            "SELECT * FROM reports" + where + " ORDER BY report_timestamp DESC, message_id DESC",
            parameters,
        ).fetchall()
        return [_row_from_record(record) for record in records]

    def counts_by_agent(self) -> list[tuple[str, int]]:
        records = self._connection.execute(
            "SELECT agent_id, COUNT(*) AS n FROM reports GROUP BY agent_id ORDER BY n DESC"
        ).fetchall()
        return [(record["agent_id"], record["n"]) for record in records]

    def counts_by_status(self) -> list[tuple[str, int]]:
        records = self._connection.execute(
            "SELECT status, COUNT(*) AS n FROM reports GROUP BY status ORDER BY n DESC"
        ).fetchall()
        return [(record["status"], record["n"]) for record in records]
