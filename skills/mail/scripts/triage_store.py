"""SQLite idempotency store for the mail triage pipeline (W4-2).

Reuses the W3-2 claim-before-send shape: a mail uid may be drafted only by the
process that wins the INSERT for its claim inside a BEGIN IMMEDIATE
transaction, so concurrent/duplicate 10-minute ticks produce at most one draft
per mail. `processed` marks terminal outcomes; `counters` tracks the
consecutive approved-send failure count that drives the mail-mode NO-GO
downgrade (2 consecutive failures → W4-2-runtime re-verdict).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Historical DB columns stay in place; new writes use zero, with no verdict.
SEND_FAILURE_COUNTER = "consecutive_send_failures"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_claims (
    uid TEXT PRIMARY KEY,
    claimed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed (
    uid TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    sensitive INTEGER NOT NULL,
    action TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS digest_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at TEXT NOT NULL,
    item_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS digest_items (
    run_id INTEGER NOT NULL,
    item_no INTEGER NOT NULL,
    uid TEXT NOT NULL,
    subject TEXT NOT NULL,
    sender_masked TEXT NOT NULL,
    sensitive INTEGER NOT NULL,
    category TEXT NOT NULL,
    flags TEXT NOT NULL,
    summary TEXT NOT NULL,
    note TEXT NOT NULL,
    recv_date TEXT NOT NULL,
    reply_key TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, item_no)
);
CREATE INDEX IF NOT EXISTS idx_digest_items_uid ON digest_items (uid);
"""
# Columns added after the first release: (name, DDL) appended in place on old DBs.
_DIGEST_ITEM_ADDED_COLUMNS = (
    ("reply_key", "reply_key TEXT NOT NULL DEFAULT ''"),
    ("message_id", "message_id TEXT NOT NULL DEFAULT ''"),
)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    connection.executescript(_SCHEMA)
    present = {row[1] for row in connection.execute("PRAGMA table_info(digest_items)")}
    for name, ddl in _DIGEST_ITEM_ADDED_COLUMNS:
        if name not in present:
            try:
                connection.execute(f"ALTER TABLE digest_items ADD COLUMN {ddl}")
            except sqlite3.OperationalError as error:  # a concurrent connection added it first
                if "duplicate column" not in str(error):
                    raise
    return connection


def claim_mail(db_path: Path, uid: str, claimed_at: str) -> bool:
    """Atomically claim a mail uid; True means this caller must process it."""
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO mail_claims (uid, claimed_at) VALUES (?, ?)",
                (uid, claimed_at),
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return cursor.rowcount == 1


def release_mail(db_path: Path, uid: str) -> None:
    """Drop a claim after a failed processing pass so a later tick retries."""
    with _connect(db_path) as connection:
        connection.execute("DELETE FROM mail_claims WHERE uid = ?", (uid,))


def record_processed(
    db_path: Path, uid: str, *, category: str, action: str, processed_at: str
) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO processed (uid, category, sensitive, action, processed_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (uid, category, 0, action, processed_at),
        )


def is_processed(db_path: Path, uid: str) -> bool:
    if not db_path.exists():
        return False
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM processed WHERE uid = ?", (uid,)
        ).fetchone()
    return row is not None


def processed_rows(db_path: Path) -> list[tuple[str, str, int, str, str]]:
    if not db_path.exists():
        return []
    with _connect(db_path) as connection:
        rows = connection.execute(
            "SELECT uid, category, sensitive, action, processed_at FROM processed ORDER BY uid"
        ).fetchall()
    return [(str(r[0]), str(r[1]), int(r[2]), str(r[3]), str(r[4])) for r in rows]


def record_digest_run(db_path: Path, sent_at: str, items: list[dict[str, int | str]]) -> int:
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute(
                "INSERT INTO digest_runs (sent_at, item_count) VALUES (?, ?)",
                (sent_at, len(items)),
            )
            if cursor.lastrowid is None:
                raise sqlite3.Error("digest run insert did not return a run id")
            run_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO digest_items "
                "(run_id, item_no, uid, subject, sender_masked, sensitive, category, "
                "flags, summary, note, recv_date, reply_key, message_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        run_id,
                        item["item_no"],
                        item["uid"],
                        item["subject"],
                        item["sender_masked"],
                        0,
                        item["category"],
                        item["flags"],
                        item["summary"],
                        item["note"],
                        item["recv_date"],
                        str(item.get("reply_key") or ""),
                        str(item.get("message_id") or ""),
                    )
                    for item in items
                ],
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return run_id


def digested_uids(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    with _connect(db_path) as connection:
        rows = connection.execute("SELECT DISTINCT uid FROM digest_items").fetchall()
    return {str(row[0]) for row in rows}


def latest_digest_items(
    db_path: Path, run_id: int | None = None
) -> list[dict[str, int | str]]:
    if not db_path.exists():
        return []
    with _connect(db_path) as connection:
        selected_run_id = run_id
        if selected_run_id is None:
            latest = connection.execute(
                "SELECT run_id FROM digest_runs ORDER BY run_id DESC LIMIT 1"
            ).fetchone()
            if latest is None:
                return []
            selected_run_id = int(latest[0])
        rows = connection.execute(
            "SELECT item_no, uid, subject, sender_masked, sensitive, category, flags, "
            "summary, note, recv_date FROM digest_items "
            "WHERE run_id = ? ORDER BY item_no",
            (selected_run_id,),
        ).fetchall()
    return [
        {
            "item_no": int(row[0]),
            "uid": str(row[1]),
            "subject": str(row[2]),
            "sender_masked": str(row[3]),
            "category": str(row[5]),
            "flags": str(row[6]),
            "summary": str(row[7]),
            "note": str(row[8]),
            "recv_date": str(row[9]),
        }
        for row in rows
    ]


def digest_item_by_reply_key(db_path: Path, reply_key: str) -> dict[str, int | str] | None:
    """The newest digest item shown with this reply key, or None."""
    if not reply_key or not db_path.exists():
        return None
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT run_id, item_no, uid, message_id FROM digest_items "
            "WHERE reply_key = ? ORDER BY run_id DESC LIMIT 1",
            (reply_key,),
        ).fetchone()
    if row is None:
        return None
    return {
        "run_id": int(row[0]), "item_no": int(row[1]),
        "uid": str(row[2]), "message_id": str(row[3]),
    }


def consecutive_send_failures(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT value FROM counters WHERE name = ?", (SEND_FAILURE_COUNTER,)
        ).fetchone()
    return int(row[0]) if row else 0


def bump_send_failures(db_path: Path) -> int:
    """Increment the consecutive-failure counter and return the new value."""
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "INSERT INTO counters (name, value) VALUES (?, 1)"
                " ON CONFLICT(name) DO UPDATE SET value = value + 1",
                (SEND_FAILURE_COUNTER,),
            )
            row = connection.execute(
                "SELECT value FROM counters WHERE name = ?", (SEND_FAILURE_COUNTER,)
            ).fetchone()
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return int(row[0])


def reset_send_failures(db_path: Path) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO counters (name, value) VALUES (?, 0)",
            (SEND_FAILURE_COUNTER,),
        )
