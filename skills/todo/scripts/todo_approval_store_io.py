"""Durable JSON persistence for todo approval generations."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Final, TypeAlias

from todo_approval_model import ApprovalState, TodoApprovalRecord

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
_FILE_MODE: Final = 0o600
_JSON_LOADS: Final[Callable[..., JsonValue]] = json.loads


class TodoApprovalStoreError(RuntimeError):
    pass


def decode(raw: str) -> TodoApprovalRecord:
    try:
        payload = _JSON_LOADS(raw)
        if not isinstance(payload, dict):
            raise TodoApprovalStoreError("approval record is not an object")
        message_id = payload["message_id"]
        outcome = payload["outcome"]
        if message_id is not None and not isinstance(message_id, str):
            raise TodoApprovalStoreError("approval message id is invalid")
        if outcome not in {None, "approved", "cancelled"}:
            raise TodoApprovalStoreError("approval outcome is invalid")
        generation = payload["generation"]
        policy_version = payload["policy_version"]
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise TodoApprovalStoreError("approval generation is invalid")
        if isinstance(policy_version, bool) or not isinstance(policy_version, int):
            raise TodoApprovalStoreError("approval policy version is invalid")
        origin_channel_id = payload.get("origin_channel_id") or ""
        origin_message_id = payload.get("origin_message_id") or ""
        if not isinstance(origin_channel_id, str) or not isinstance(origin_message_id, str):
            raise TodoApprovalStoreError("approval origin binding is invalid")
        # 요청별 승인 스레드가 생기기 전에 쓰인 레코드는 이 열이 없다 — 빈 값으로 읽고
        # 결과 통지는 예전처럼 origin/저장된 채널로 간다.
        approval_thread_id = payload.get("approval_thread_id") or ""
        if not isinstance(approval_thread_id, str):
            raise TodoApprovalStoreError("approval thread binding is invalid")
        tasklist = payload.get("tasklist") or ""
        title = payload.get("title") or ""
        if not isinstance(tasklist, str) or not isinstance(title, str):
            raise TodoApprovalStoreError("approval execution parameters are invalid")
        notes = _optional_string(payload, "notes")
        due = _optional_string(payload, "due")
        return TodoApprovalRecord(
            _required_string(payload, "key"),
            generation,
            _required_string(payload, "action_hash"),
            _required_string(payload, "target_id"),
            _required_string(payload, "argv_summary"),
            message_id,
            _datetime_from_iso(_required_string(payload, "created_at")),
            ApprovalState(_required_string(payload, "state")),
            outcome,
            _required_string(payload, "kind"),
            _required_string(payload, "surface"),
            _required_string(payload, "channel_id"),
            policy_version,
            origin_channel_id=origin_channel_id,
            origin_message_id=origin_message_id,
            approval_thread_id=approval_thread_id,
            tasklist=tasklist,
            title=title,
            notes=notes,
            due=due,
        )
    except (KeyError, ValueError, json.JSONDecodeError) as error:
        raise TodoApprovalStoreError("approval record is malformed") from error


def atomic_write(path: Path, record: TodoApprovalRecord) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            _ = handle.write(_encoded(record))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(_FILE_MODE)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def exclusive_write(path: Path, record: TodoApprovalRecord) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    except FileExistsError as error:
        try:
            existing = decode(path.read_text(encoding="utf-8"))
        except OSError as read_error:
            raise TodoApprovalStoreError(f"archive generation is unreadable: {path}") from read_error
        if existing == record:
            return
        raise TodoApprovalStoreError(f"archive generation already exists: {path}") from error
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        _ = handle.write(_encoded(record))
        handle.flush()
        os.fsync(handle.fileno())


def _datetime_from_iso(raw: str) -> datetime:
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None:
        raise TodoApprovalStoreError("approval timestamp lacks timezone")
    return value.astimezone(UTC)


def _encoded(record: TodoApprovalRecord) -> str:
    return json.dumps(_payload(record), separators=(",", ":"), sort_keys=True)


def _payload(record: TodoApprovalRecord) -> dict[str, JsonValue]:
    return {
        "key": record.key,
        "generation": record.generation,
        "action_hash": record.action_hash,
        "target_id": record.target_id,
        "argv_summary": record.argv_summary,
        "message_id": record.message_id,
        "created_at": record.created_at.isoformat(),
        "state": record.state.value,
        "outcome": record.outcome,
        "kind": record.kind,
        "surface": record.surface,
        "channel_id": record.channel_id,
        "policy_version": record.policy_version,
        "origin_channel_id": record.origin_channel_id,
        "origin_message_id": record.origin_message_id,
        "approval_thread_id": record.approval_thread_id,
        "tasklist": record.tasklist,
        "title": record.title,
        "notes": record.notes,
        "due": record.due,
    }


def _optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    """Read a nullable execution parameter, tolerating records written before it existed."""
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TodoApprovalStoreError(f"approval record has a non-string {key}")
    return value


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise TodoApprovalStoreError(f"approval record omitted {key}")
    return value
