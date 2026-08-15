from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final


_SCHEMA: Final = (
    "CREATE TABLE IF NOT EXISTS receipts (channel_id TEXT NOT NULL, "
    "message_id TEXT PRIMARY KEY, state TEXT NOT NULL)"
)
_RECEIVED: Final = "received"
_RESOLVED_OK: Final = "resolved_ok"
_RESOLVED_FAIL: Final = "resolved_fail"
_RECORD_RECEIVED: Final = (
    "INSERT INTO receipts (channel_id, message_id, state) VALUES (?, ?, 'received') "
    "ON CONFLICT(message_id) DO UPDATE SET channel_id = excluded.channel_id, state = 'received'"
)


def _prepare_parent(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)


def _state_rows(db_path: Path) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(db_path)) as connection:
        rows: list[tuple[str, str]] = list(
            connection.execute("SELECT message_id, state FROM receipts")
        )
    return rows


def default_ledger_path() -> Path:
    directory = Path.home() / ".hermes" / "owner-dm-receipts"
    _prepare_parent(directory)
    return directory / "receipts.sqlite3"


@dataclass(frozen=True, slots=True)
class ReceiptLedger:
    db_path: Path

    def __post_init__(self) -> None:
        _prepare_parent(self.db_path.parent)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            _ = connection.execute(_SCHEMA)
        self.db_path.chmod(0o600)

    def record_received(self, channel_id: str, message_id: str) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            _ = connection.execute(_RECORD_RECEIVED, (channel_id, message_id))

    def resolve(self, message_id: str, ok: bool) -> None:
        state = _RESOLVED_OK if ok else _RESOLVED_FAIL
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            _ = connection.execute(
                "UPDATE receipts SET state = ? WHERE message_id = ?",
                (state, message_id),
            )

    def reconcile_unresolved(self) -> int:
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            result = connection.execute(
                "UPDATE receipts SET state = ? WHERE state = ?",
                (_RESOLVED_FAIL, _RECEIVED),
            )
            return result.rowcount

    def states(self) -> dict[str, str]:
        receipt_states: dict[str, str] = {}
        for message_id, state in _state_rows(self.db_path):
            receipt_states[message_id] = state
        return receipt_states
