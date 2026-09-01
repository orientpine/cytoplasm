"""Approved todo execution with generation-scoped retry suppression."""
from __future__ import annotations

import fcntl
import hashlib
import importlib
import json
import math
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias, cast

from todo_approval import ApprovalRuntime, DirectoryLike, TransportLike, _repo_module, surface_module
from todo_approval_store import ApprovalState, TodoApprovalRecord, TodoApprovalStore

if TYPE_CHECKING:
    from automation.interop.approval_lease import ApprovalLease


JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
RecordApproval: TypeAlias = Callable[[Path, TodoApprovalRecord, str, datetime], bool]
_FILE_MODE = 0o600
_SETTLED_OUTCOMES = frozenset({"already-verified", "legacy-unreplayable"})
_INITIAL_RETRY_DELAY = timedelta(minutes=2)
_MAX_RETRY_DELAY = timedelta(hours=1)


def append_manual_approval(path: Path, record: TodoApprovalRecord, owner_id: str, now: datetime) -> bool:
    payload: dict[str, JsonValue] = {
        "action": "external_effect.approval",
        "approval": {
            "channel": "approvals",
            "message_id": record.message_id,
            "method": "manual_reaction",
            "owner_id": owner_id,
        },
        "hash": record.action_hash,
        "result": {"status": "approved"},
        "target_id": record.target_id,
        "timestamp": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            if any(_same_approval(line, payload) for line in handle):
                return False
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    path.chmod(_FILE_MODE)
    return True


def _same_approval(raw: str, expected: dict[str, JsonValue]) -> bool:
    try:
        actual = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(actual, dict) and all(
        actual.get(key) == expected[key] for key in ("action", "approval", "hash", "result", "target_id")
    )


class ExecutionFailureJournal:
    """Persist retry state per approval generation, like reminder claims per message."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, record: TodoApprovalRecord) -> Path:
        identity = f"{record.key}\0{record.generation}".encode()
        return self.root / f"{hashlib.sha256(identity).hexdigest()}.retry.json"

    def retry_due(self, record: TodoApprovalRecord, now: datetime) -> bool:
        return now.timestamp() >= self._read(record)[1]

    def record_failure(self, record: TodoApprovalRecord, now: datetime) -> None:
        failures, _ = self._read(record)
        delay = min(_INITIAL_RETRY_DELAY * 2 ** min(failures, 5), _MAX_RETRY_DELAY)
        self._write(record, failures + 1, now.timestamp() + delay.total_seconds())

    def clear(self, record: TodoApprovalRecord) -> None:
        self._path(record).unlink(missing_ok=True)

    def _read(self, record: TodoApprovalRecord) -> tuple[int, float]:
        try:
            data = json.loads(self._path(record).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0, 0.0
        if not isinstance(data, dict) or data.get("key") != record.key or data.get("generation") != record.generation:
            return 0, 0.0
        failures, retry_at = data.get("consecutive_failures"), data.get("next_retry_at")
        if not isinstance(failures, int) or isinstance(failures, bool) or failures < 0:
            return 0, 0.0
        if (
            not isinstance(retry_at, (int, float))
            or isinstance(retry_at, bool)
            or not math.isfinite(retry_at)
        ):
            return 0, 0.0
        return failures, float(retry_at)

    def _write(self, record: TodoApprovalRecord, failures: int, retry_at: float) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        path = self._path(record)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps({
                "consecutive_failures": failures,
                "generation": record.generation,
                "key": record.key,
                "next_retry_at": retry_at,
                "version": 1,
            }, sort_keys=True) + "\n", encoding="utf-8")
            temporary.chmod(_FILE_MODE)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


def execute_approved_writes(
    *,
    store: TodoApprovalStore,
    approval_log: Path,
    owner_id: str,
    runner: object | None = None,
    now: datetime | None = None,
    lease: ApprovalLease | None = None,
) -> tuple[tuple[str, str], ...]:
    """Execute approved writes once, backing off only retryable create failures."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    todo = importlib.import_module("todo_cli")
    claims = importlib.import_module("todo_execution_claim").ApprovalClaimStore(store.root)
    context = todo.gate_module().ApprovalContext(
        approval_log=approval_log, owner_id=owner_id, e2e_test_mode=os.environ.get("E2E_TEST_MODE") == "1"
    )
    lease = lease or _repo_module("approval_lease").FileKeyLease(store.root / "approval-leases")
    journal = ExecutionFailureJournal(store.root / "execution-retry-journal")
    results: list[tuple[str, str]] = []
    for record in store.latest_archives():
        if record.state is not ApprovalState.ARCHIVED or record.outcome != "approved":
            continue
        with lease.hold(record.key) as owned:
            if not owned:
                continue
            if not journal.retry_due(record, moment):
                results.append((record.key, "backoff"))
                continue
            outcome = _execute_one(record, todo, claims, context, runner)
            results.append((record.key, outcome))
            if outcome.startswith("failed:"):
                journal.record_failure(record, moment)
            else:
                journal.clear(record)
            if outcome not in _SETTLED_OUTCOMES:
                print(f"TODO-EXEC {outcome} key={record.key} gen={record.generation}", file=sys.stderr)
    return tuple(results)


def _execute_one(record: TodoApprovalRecord, todo: object, claims: object, context: object, runner: object | None) -> str:
    if not record.title:
        return "legacy-unreplayable"
    try:
        state = claims.status(record)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — an unreadable receipt must not stop other rows
        return "claim-unreadable"
    if state == "verified":
        return "already-verified"
    if state == "write_started":
        return "reconcile-required"
    request = todo.TaskRequest(  # type: ignore[attr-defined]
        record.tasklist or "@default", record.title, record.notes, record.due
    )
    try:
        decision = todo.evaluate(todo.insert_argv(request), context=context)  # type: ignore[attr-defined]
        if decision.action_hash != record.action_hash:
            return "hash-mismatch"
        todo.create_task(request, runner=runner, context=context, claim_store=claims)  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001 — a failed write leaves no verified receipt
        return f"failed:{type(error).__name__}"
    return "written"


def build_runtime(
    store: TodoApprovalStore,
    transport: object,
    directory: DirectoryLike,
    owner_id: str,
    lease: ApprovalLease,
    record: TodoApprovalRecord,
    now: datetime,
) -> ApprovalRuntime:
    surface = surface_module()
    binding = surface.ApprovalBinding(
        surface.ApprovalKind(record.kind), surface.ApprovalSurface(record.surface),
        record.channel_id, record.policy_version,
    )
    journal = _repo_module("approval_lease").PostingJournal(store.root / "posting-journal")
    return ApprovalRuntime(
        store, cast(TransportLike, transport), directory, owner_id, binding, lease, journal, lambda: now
    )
