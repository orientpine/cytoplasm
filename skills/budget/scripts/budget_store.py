"""SQLite snapshot/claim/retry store for the budget watcher (W4-3).

Idempotency reuses the W3-2 claim-before-send shape: a snapshot diff may draft
a request mail only after winning the INSERT for its (prev,new) claim key
inside a BEGIN IMMEDIATE transaction, so concurrent/duplicate ticks produce
exactly one draft per change. Sheet-access failures append durable retry-queue
rows that the next successful read resolves (no silent drop).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    taken_at TEXT NOT NULL,
    hash TEXT NOT NULL,
    rows_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS change_claims (
    claim_key TEXT PRIMARY KEY,
    claimed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retry_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reason TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    resolved_at TEXT
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    connection.executescript(_SCHEMA)
    return connection


def latest_snapshot(db_path: Path) -> tuple[str, list[tuple[str, ...]]] | None:
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT hash, rows_json FROM snapshots ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return str(row[0]), [tuple(item) for item in json.loads(str(row[1]))]


def store_snapshot(db_path: Path, snapshot_hash: str, rows: list[tuple[str, ...]], taken_at: str) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            "INSERT INTO snapshots (taken_at, hash, rows_json) VALUES (?, ?, ?)",
            (taken_at, snapshot_hash, json.dumps([list(row) for row in rows], ensure_ascii=False)),
        )


def claim_change(db_path: Path, claim_key: str, claimed_at: str) -> bool:
    """Atomically claim a change; True means this caller must draft."""
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO change_claims (claim_key, claimed_at) VALUES (?, ?)",
                (claim_key, claimed_at),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return cursor.rowcount == 1


def release_change(db_path: Path, claim_key: str) -> None:
    """Drop a claim after a failed draft so a later tick can retry."""
    with _connect(db_path) as connection:
        connection.execute("DELETE FROM change_claims WHERE claim_key = ?", (claim_key,))


def queue_retry(db_path: Path, reason: str, queued_at: str) -> int:
    with _connect(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO retry_queue (reason, queued_at) VALUES (?, ?)", (reason, queued_at)
        )
        return int(cursor.lastrowid or 0)


def pending_retries(db_path: Path) -> list[tuple[int, str, str]]:
    if not db_path.exists():
        return []
    with _connect(db_path) as connection:
        rows = connection.execute(
            "SELECT id, reason, queued_at FROM retry_queue WHERE resolved_at IS NULL ORDER BY id"
        ).fetchall()
    return [(int(r[0]), str(r[1]), str(r[2])) for r in rows]


def resolve_retries(db_path: Path, resolved_at: str) -> int:
    with _connect(db_path) as connection:
        cursor = connection.execute(
            "UPDATE retry_queue SET resolved_at = ? WHERE resolved_at IS NULL", (resolved_at,)
        )
        return cursor.rowcount
