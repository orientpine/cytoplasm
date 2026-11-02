"""Read-only lists of dashboard todos and orders for YooDori's node gate (owner Q454, 2026-11-02).

YooDori works the dashboard as her task list: an order the owner wrote is his command, a todo extracted from
mail or minutes (``source_kind``) is asked about before anything is written. These lists open the board files
read-only (never create, migrate or touch them) and carry bounded fields only: a mail subject cut short with any
address in it masked, never a sender or body, never an order author login, device id or address.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Final

from .models import DEFAULT_GRADE, OPEN_STATUSES, STATUSES
from .orders import OPEN_ORDER_STATUSES, ORDER_STATUSES, ORDERS_FILE, OWNER_DEVICE

TODO_NOTE_LIMIT: Final = 200
SUBJECT_LIMIT: Final = 120
ORDER_BODY_LIMIT: Final = 500
LIST_LIMIT: Final = 200
_ADDRESS: Final = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _clip(text: str, limit: int) -> tuple[str, bool]:
    flat = " ".join(text.split())
    return (flat, False) if len(flat) <= limit else (flat[: limit - 1] + "…", True)


def _rows(path: Path, query: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    if not path.exists():
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        connection.row_factory = sqlite3.Row
        return connection.execute(query, params).fetchall()
    finally:
        connection.close()


def todo_items(root: Path, *, open_only: bool) -> list[dict[str, object]]:
    statuses = sorted(OPEN_STATUSES) if open_only else list(STATUSES)
    rows = _rows(
        root / "board.db",
        "SELECT todos.*, COALESCE(tasks.grade, ?) AS grade FROM todos"
        " LEFT JOIN tasks ON tasks.task_key = todos.task_key"
        f" WHERE todos.status IN ({','.join('?' * len(statuses))})"
        " ORDER BY due IS NULL, due, todos.project, todo_id LIMIT ?",
        (DEFAULT_GRADE, *statuses, LIST_LIMIT),
    )
    items = []
    for row in rows:
        note, cut = _clip(str(row["note"]), TODO_NOTE_LIMIT)
        items.append({
            "todo_id": str(row["todo_id"]), "project": str(row["project"]), "title": str(row["title"]),
            "owner": str(row["owner"]), "due": row["due"], "status": str(row["status"]),
            "progress": int(row["progress"]), "source_kind": str(row["source_kind"]),
            "source_id": str(row["source_id"]),
            "source_subject": _clip(_ADDRESS.sub("[주소]", str(row["source_subject"])), SUBJECT_LIMIT)[0],
            "approved_at": str(row["approved_at"]),
            "updated_at": str(row["updated_at"]), "grade": str(row["grade"]),
            "note": note, "note_truncated": cut,
        })
    return items


def order_items(root: Path, *, open_only: bool) -> list[dict[str, object]]:
    statuses = sorted(OPEN_ORDER_STATUSES) if open_only else list(ORDER_STATUSES)
    rows = _rows(
        root / ORDERS_FILE,
        f"SELECT * FROM orders WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY number DESC LIMIT ?",
        (*statuses, LIST_LIMIT),
    )
    items = []
    for row in reversed(rows):
        body, cut = _clip(str(row["body"]), ORDER_BODY_LIMIT)
        items.append({
            "number": int(row["number"]), "created_at": str(row["created_at"]), "status": str(row["status"]),
            "body": body, "body_truncated": cut, "todo_id": str(row["todo_id"]), "project": str(row["project"]),
            "via": str(row["via"]), "owner_device": str(row["via"]) == OWNER_DEVICE,
            "receiver": str(row["receiver"]), "dori": str(row["dori"]), "post_url": str(row["post_url"]),
            "delivered_to": str(row["delivered_to"]), "updated_at": str(row["updated_at"]),
        })
    return items


def todo_line(item: dict[str, object]) -> str:
    return (f"TODO {item['todo_id']} status={item['status']} progress={item['progress']} due={item['due'] or '미정'} "
            f"source={item['source_kind']} approved={item['approved_at']} project={item['project']} "
            f"title={item['title']}")


def order_line(item: dict[str, object]) -> str:
    return (f"ORDER #{item['number']} status={item['status']} via={item['via']} todo={item['todo_id'] or '-'} "
            f"dori={item['dori'] or '-'} created={item['created_at']} body={item['body']}")
