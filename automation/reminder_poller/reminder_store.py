"""Concurrency-safe SQLite idempotency store for the reminder poller (W3-2).

Claim-before-send: a poller may DM only after it wins the INSERT for the
reminder key inside a BEGIN IMMEDIATE transaction. Two concurrent pollers
racing on the same key produce exactly one winner (SQLite write lock), so
duplicate invocations send zero duplicates. A failed send releases the claim
so the next poll can retry.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_reminders (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (kind, key)
)
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    connection.execute(_SCHEMA)
    return connection


def claim(db_path: Path, kind: str, key: str) -> bool:
    """Atomically claim a reminder key; True means this caller must send."""
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO sent_reminders (kind, key, sent_at) VALUES (?, ?, ?)",
                (kind, key, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return cursor.rowcount == 1


def release(db_path: Path, kind: str, key: str) -> None:
    """Drop a claim after a failed send so a later poll can retry."""
    with _connect(db_path) as connection:
        connection.execute(
            "DELETE FROM sent_reminders WHERE kind = ? AND key = ?", (kind, key)
        )


def rows(db_path: Path) -> list[tuple[str, str, str]]:
    """All (kind, key, sent_at) rows — evidence/inspection helper."""
    if not db_path.exists():
        return []
    with _connect(db_path) as connection:
        cursor = connection.execute(
            "SELECT kind, key, sent_at FROM sent_reminders ORDER BY sent_at, kind, key"
        )
        return [(str(k), str(key), str(at)) for k, key, at in cursor.fetchall()]
