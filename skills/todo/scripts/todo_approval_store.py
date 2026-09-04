"""Generation-preserving pending and archive store for todo approvals."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import todo_approval_store_io as _io
from todo_approval_model import ApprovalState, TodoApprovalRecord, TodoApprovalSpec


TODO_APPROVAL_TTL: Final = timedelta(hours=24)
TodoApprovalStoreError = _io.TodoApprovalStoreError


def approval_ttl(env: Mapping[str, str] = os.environ) -> timedelta:
    raw = env.get("TODO_APPROVAL_TTL")
    if raw is None:
        return TODO_APPROVAL_TTL
    try:
        seconds = int(raw)
    except ValueError as error:
        raise TodoApprovalStoreError("TODO_APPROVAL_TTL must be integer seconds") from error
    if seconds <= 0:
        raise TodoApprovalStoreError("TODO_APPROVAL_TTL must be positive")
    return timedelta(seconds=seconds)


@dataclass(frozen=True, slots=True)
class TodoApprovalStore:
    root: Path

    def pending_path(self, key: str) -> Path:
        return self.root / "pending" / f"{_slug(key)}.json"

    def archive_path(self, key: str, generation: int) -> Path:
        return self.root / "archive" / _slug(key) / f"{generation}.json"

    def active(self, key: str) -> TodoApprovalRecord | None:
        path = self.pending_path(key)
        try:
            return _io.decode(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except OSError as error:
            raise TodoApprovalStoreError(f"pending approval is unreadable: {path}") from error

    def archives(self, key: str) -> tuple[TodoApprovalRecord, ...]:
        directory = self.archive_path(key, 0).parent
        if not directory.is_dir():
            return ()
        records: list[TodoApprovalRecord] = []
        for path in sorted(directory.glob("*.json"), key=_generation_from_path):
            try:
                record = _io.decode(path.read_text(encoding="utf-8"))
            except OSError as error:
                raise TodoApprovalStoreError(f"archived approval is unreadable: {path}") from error
            if record.state is ApprovalState.PENDING:
                raise TodoApprovalStoreError(f"archive contains pending state: {path}")
            records.append(record)
        return tuple(records)

    def latest_archives(self) -> tuple[TodoApprovalRecord, ...]:
        """Newest archived generation per key — the execution reconciler's candidates.

        Only the newest generation can still be unexecuted: an older one was either
        superseded or already written, and ``ApprovalClaimStore`` keys its receipt by
        generation, so replaying an older row could never match the ledger anyway.
        """
        directory = self.root / "archive"
        if not directory.is_dir():
            return ()
        records: list[TodoApprovalRecord] = []
        for key_directory in sorted(directory.iterdir()):
            if not key_directory.is_dir():
                continue
            generations = sorted(key_directory.glob("*.json"), key=_generation_from_path)
            if not generations:
                continue
            path = generations[-1]
            try:
                records.append(_io.decode(path.read_text(encoding="utf-8")))
            except OSError as error:
                raise TodoApprovalStoreError(f"archived approval is unreadable: {path}") from error
        return tuple(records)

    def outstanding(self, key: str) -> tuple[TodoApprovalRecord, ...]:
        record = self.active(key)
        if record is None or record.state is not ApprovalState.PENDING or record.message_id is None:
            return ()
        return (record,)

    def all_outstanding(self) -> tuple[TodoApprovalRecord, ...]:
        directory = self.root / "pending"
        if not directory.is_dir():
            return ()
        records: list[TodoApprovalRecord] = []
        for path in sorted(directory.glob("*.json")):
            try:
                record = _io.decode(path.read_text(encoding="utf-8"))
            except OSError as error:
                raise TodoApprovalStoreError(f"pending approval is unreadable: {path}") from error
            if record.state is not ApprovalState.PENDING:
                raise TodoApprovalStoreError(f"pending slot contains terminal state: {path}")
            if record.message_id is not None:
                records.append(record)
        return tuple(records)

    def prepare(self, spec: TodoApprovalSpec, now: datetime) -> TodoApprovalRecord:
        moment = _utc(now)
        current = self.active(spec.key)
        if current is not None:
            _assert_spec(current, spec)
            if moment - current.created_at < approval_ttl():
                return current
            _ = self.archive(current, ApprovalState.EXPIRED, None)
        generation = max((record.generation for record in self.archives(spec.key)), default=0) + 1
        record = TodoApprovalRecord(
            key=spec.key,
            generation=generation,
            action_hash=spec.action_hash,
            target_id=spec.target_id,
            argv_summary=spec.argv_summary,
            message_id=None,
            created_at=moment,
            state=ApprovalState.PENDING,
            outcome=None,
            kind=spec.kind,
            surface=spec.surface,
            channel_id=spec.channel_id,
            policy_version=spec.policy_version,
            origin_channel_id=spec.origin_channel_id,
            origin_message_id=spec.origin_message_id,
            approval_thread_id=spec.approval_thread_id,
            tasklist=spec.tasklist,
            title=spec.title,
            notes=spec.notes,
            due=spec.due,
        )
        _io.atomic_write(self.pending_path(spec.key), record)
        return record

    def bind_message(self, record: TodoApprovalRecord, message_id: str) -> TodoApprovalRecord:
        if not message_id:
            raise TodoApprovalStoreError("message id is empty")
        current = self.active(record.key)
        if current is None or current != record:
            raise TodoApprovalStoreError("pending generation changed before message binding")
        if current.message_id is not None:
            if current.message_id == message_id:
                return current
            raise TodoApprovalStoreError("pending message id is already bound")
        bound = replace(current, message_id=message_id)
        _io.atomic_write(self.pending_path(record.key), bound)
        return bound

    def archive(
        self,
        record: TodoApprovalRecord,
        state: ApprovalState,
        outcome: str | None,
    ) -> TodoApprovalRecord:
        if state is ApprovalState.PENDING:
            raise TodoApprovalStoreError("pending state cannot be archived")
        if outcome not in {None, "approved", "cancelled"}:
            raise TodoApprovalStoreError("approval outcome is invalid")
        if state is ApprovalState.EXPIRED and outcome is not None:
            raise TodoApprovalStoreError("expired approval cannot carry an owner outcome")
        current = self.active(record.key)
        if current is None or current != record:
            raise TodoApprovalStoreError("pending generation changed before archive")
        archived = replace(current, state=state, outcome=outcome)
        path = self.archive_path(record.key, record.generation)
        _io.exclusive_write(path, archived)
        self.pending_path(record.key).unlink()
        return archived


def _slug(key: str) -> str:
    from automation.interop.approval_lease import slug

    return slug(key)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise TodoApprovalStoreError("approval timestamp lacks timezone")
    return value.astimezone(UTC)


def _assert_spec(record: TodoApprovalRecord, spec: TodoApprovalSpec) -> None:
    actual = (
        record.key,
        record.action_hash,
        record.target_id,
        record.argv_summary,
        record.kind,
        record.surface,
        record.channel_id,
        record.policy_version,
    )
    expected = (
        spec.key,
        spec.action_hash,
        spec.target_id,
        spec.argv_summary,
        spec.kind,
        spec.surface,
        spec.channel_id,
        spec.policy_version,
    )
    if actual != expected:
        raise TodoApprovalStoreError("pending approval binding changed")


def _generation_from_path(path: Path) -> int:
    try:
        return int(path.stem)
    except ValueError as error:
        raise TodoApprovalStoreError(f"archive generation is invalid: {path.name}") from error
