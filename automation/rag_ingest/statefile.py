"""Durable ingest state: per-document fingerprints, point ids, cursors.

State is the client-side dedup layer: a document whose fingerprint is
unchanged is skipped before any network call. The server-side uuid5 upsert is
the second, independent dedup layer. State is only advanced AFTER successful
delivery, so a crash or RAG outage can never lose work (at-least-once with
idempotent upserts).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

STATE_VERSION = 1


def empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "documents": {}, "cursors": {}}


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return empty_state()
    loaded.setdefault("version", STATE_VERSION)
    loaded.setdefault("documents", {})
    loaded.setdefault("cursors", {})
    return loaded


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Atomic write (tmp + rename) with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def document_fingerprint(state: dict[str, Any], source_key: str) -> str | None:
    entry = state["documents"].get(source_key)
    if isinstance(entry, dict):
        fingerprint = entry.get("fingerprint")
        if isinstance(fingerprint, str):
            return fingerprint
    return None


def document_point_ids(state: dict[str, Any], source_key: str) -> list[str]:
    entry = state["documents"].get(source_key)
    if isinstance(entry, dict):
        point_ids = entry.get("point_ids")
        if isinstance(point_ids, list):
            return [str(point_id) for point_id in point_ids]
    return []


def record_document(
    state: dict[str, Any],
    source_key: str,
    fingerprint: str,
    point_ids: list[str],
    ingested_at: str,
) -> None:
    state["documents"][source_key] = {
        "fingerprint": fingerprint,
        "point_ids": point_ids,
        "ingested_at": ingested_at,
    }


def remove_document(state: dict[str, Any], source_key: str) -> None:
    state["documents"].pop(source_key, None)


def apply_cursor_updates(state: dict[str, Any], cursor_updates: dict[str, str]) -> None:
    for key, value in cursor_updates.items():
        state["cursors"][key] = value
