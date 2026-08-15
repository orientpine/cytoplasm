from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:

    class ReceiptLedger:
        def __init__(self, db_path: Path) -> None:
            _ = db_path

        def record_received(self, channel_id: str, message_id: str) -> None:
            _ = channel_id
            _ = message_id

        def resolve(self, message_id: str, ok: bool) -> None:
            _ = message_id
            _ = ok

        def reconcile_unresolved(self) -> int:
            return 0

        def states(self) -> dict[str, str]:
            return {}

else:
    from automation.hermes_compat.receipt_ledger import ReceiptLedger


def _schema_columns(db_path: Path) -> set[str]:
    with closing(sqlite3.connect(db_path)) as connection:
        rows: list[tuple[object, str, object, object, object, object]] = list(
            connection.execute("PRAGMA table_info(receipts)")
        )
    return {row[1] for row in rows}


def _receipt_count(db_path: Path) -> int:
    with closing(sqlite3.connect(db_path)) as connection:
        rows: list[tuple[int]] = list(connection.execute("SELECT COUNT(*) FROM receipts"))
    return rows[0][0]


def test_schema_contains_only_content_free_columns(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receipts.sqlite3"
    _ = ReceiptLedger(db_path)

    # When
    columns = _schema_columns(db_path)

    # Then
    assert columns == {"channel_id", "message_id", "state"}


def test_record_received_stores_received_state(tmp_path: Path) -> None:
    # Given
    ledger = ReceiptLedger(tmp_path / "receipts.sqlite3")

    # When
    ledger.record_received("channel-1", "message-1")

    # Then
    assert ledger.states() == {"message-1": "received"}


def test_resolve_marks_receipt_ok(tmp_path: Path) -> None:
    # Given
    ledger = ReceiptLedger(tmp_path / "receipts.sqlite3")
    ledger.record_received("channel-1", "message-1")

    # When
    ledger.resolve("message-1", ok=True)

    # Then
    assert ledger.states() == {"message-1": "resolved_ok"}


def test_resolve_marks_receipt_failed(tmp_path: Path) -> None:
    # Given
    ledger = ReceiptLedger(tmp_path / "receipts.sqlite3")
    ledger.record_received("channel-1", "message-1")

    # When
    ledger.resolve("message-1", ok=False)

    # Then
    assert ledger.states() == {"message-1": "resolved_fail"}


def test_reconcile_unresolved_marks_every_received_receipt_failed(tmp_path: Path) -> None:
    # Given
    ledger = ReceiptLedger(tmp_path / "receipts.sqlite3")
    ledger.record_received("channel-1", "message-1")
    ledger.record_received("channel-1", "message-2")

    # When
    reconciled = ledger.reconcile_unresolved()

    # Then
    assert reconciled == 2
    assert ledger.states() == {
        "message-1": "resolved_fail",
        "message-2": "resolved_fail",
    }


def test_receipts_persist_across_new_ledger_instances(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receipts.sqlite3"
    first_ledger = ReceiptLedger(db_path)
    first_ledger.record_received("channel-1", "message-1")

    # When
    second_ledger = ReceiptLedger(db_path)

    # Then
    assert second_ledger.states() == {"message-1": "received"}


def test_record_received_is_idempotent_for_a_message(tmp_path: Path) -> None:
    # Given
    db_path = tmp_path / "receipts.sqlite3"
    ledger = ReceiptLedger(db_path)
    ledger.record_received("channel-1", "message-1")

    # When
    ledger.record_received("channel-1", "message-1")

    # Then
    assert _receipt_count(db_path) == 1
    assert ledger.states() == {"message-1": "received"}
