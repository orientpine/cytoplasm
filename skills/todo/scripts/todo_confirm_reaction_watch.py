#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias, cast


_LIVE_SCRIPTS: Final = "/srv/autophagy-skills/live/todo/scripts"
_SCRIPTS = Path(os.environ.get("TODO_SCRIPTS", _LIVE_SCRIPTS)).expanduser()
if _SCRIPTS.is_dir() and str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from todo_approval import (  # noqa: E402
    ApprovalRuntime,
    DirectoryLike,
    TodoApprovalGate,
    TodoApprovalIntent,
    TransportLike,
    _repo_module,
    lifecycle,
    surface_module,
)
from todo_approval_store import (  # noqa: E402
    ApprovalState,
    TodoApprovalRecord,
    TodoApprovalStore,
    approval_ttl,
)

if TYPE_CHECKING:
    from automation.interop.approval_lease import ApprovalLease
    from automation.interop.approval_lifecycle import ApprovalRequest, Probe


JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
RecordApproval: TypeAlias = Callable[[Path, TodoApprovalRecord, str, datetime], bool]
_FILE_MODE: Final = 0o600
_ENV_SECRETS: Final = Path.home() / ".env.secrets"


class TodoWatchError(RuntimeError):
    pass


class ReactionTransport(Protocol):
    def get_message(self, channel_id: str, message_id: str) -> str | None: ...

    def get_reaction_users(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> tuple[tuple[str, bool], ...]: ...


def append_manual_approval(
    path: Path,
    record: TodoApprovalRecord,
    owner_id: str,
    now: datetime,
) -> bool:
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
    if not isinstance(actual, dict):
        return False
    return all(actual.get(key) == expected[key] for key in ("action", "approval", "hash", "result", "target_id"))


@dataclass(slots=True)
class TodoOwnerDecision:
    record: TodoApprovalRecord
    gate: TodoApprovalGate
    store: TodoApprovalStore
    approval_log: Path
    owner_id: str
    now: datetime
    record_approval: RecordApproval
    outcome: str | None = None

    def probe(self, request: ApprovalRequest) -> Probe:
        return self.gate.probe(request)

    def apply(self, request: ApprovalRequest, decision: Probe) -> None:
        del request
        probe = lifecycle().Probe
        if decision is probe.APPROVED:
            self.record_approval(self.approval_log, self.record, self.owner_id, self.now)
            self.outcome = "approved"
        elif decision is probe.CANCELLED:
            self.outcome = "cancelled"
        else:
            raise TodoWatchError("todo owner decision is not terminal")

    def drop(self, request: ApprovalRequest) -> None:
        if self.outcome not in {"approved", "cancelled"}:
            raise TodoWatchError("todo owner decision has no terminal outcome")
        current = self.store.active(request.key)
        if current != self.record:
            raise TodoWatchError("todo pending generation changed before decision archive")
        self.store.archive(self.record, ApprovalState.ARCHIVED, self.outcome)


def run_once(
    *,
    store: TodoApprovalStore,
    owner_id: str,
    transport: ReactionTransport,
    directory: DirectoryLike,
    approval_log: Path,
    lease: ApprovalLease,
    now: datetime,
    record_approval: RecordApproval = append_manual_approval,
) -> None:
    moment = now.astimezone(UTC)
    try:
        records = store.all_outstanding()
        for record in records:
            if moment - record.created_at >= approval_ttl():
                _expire(store, lease, record)
                continue
            request = _request(record)
            runtime = _runtime(store, transport, directory, owner_id, lease, record, moment)
            decision = TodoOwnerDecision(
                record,
                TodoApprovalGate(_intent(record), runtime),
                store,
                approval_log,
                owner_id,
                moment,
                record_approval,
            )
            lifecycle().resolve_owner_decision(request, decision, lease)
    except (OSError, RuntimeError, ValueError) as error:
        raise TodoWatchError("todo approval watcher failed closed") from error


def _expire(store: TodoApprovalStore, lease: ApprovalLease, record: TodoApprovalRecord) -> None:
    with lease.hold(record.key) as owned:
        if not owned:
            return
        current = store.active(record.key)
        if current == record:
            store.archive(record, ApprovalState.EXPIRED, None)


def _request(record: TodoApprovalRecord) -> ApprovalRequest:
    if record.message_id is None:
        raise TodoWatchError("todo pending generation has no message binding")
    return lifecycle().ApprovalRequest(
        record.key,
        record.action_hash,
        record.message_id,
        record.channel_id,
        record.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _intent(record: TodoApprovalRecord) -> TodoApprovalIntent:
    return TodoApprovalIntent(record.action_hash, record.target_id, record.argv_summary, "", None)


def _runtime(
    store: TodoApprovalStore,
    transport: ReactionTransport,
    directory: DirectoryLike,
    owner_id: str,
    lease: ApprovalLease,
    record: TodoApprovalRecord,
    now: datetime,
) -> ApprovalRuntime:
    surface = surface_module()
    binding = surface.ApprovalBinding(
        surface.ApprovalKind(record.kind),
        surface.ApprovalSurface(record.surface),
        record.channel_id,
        record.policy_version,
    )
    journal = _repo_module("approval_lease").PostingJournal(store.root / "posting-journal")
    return ApprovalRuntime(
        store,
        cast(TransportLike, transport),
        directory,
        owner_id,
        binding,
        lease,
        journal,
        lambda: now,
    )


def _load_env_secrets(path: Path = _ENV_SECRETS) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def main() -> int:
    try:
        _load_env_secrets()
        token = os.environ.get("DISCORD_BOT_TOKEN", "")
        if not token:
            raise TodoWatchError("todo Discord identity is unavailable")
        todo = __import__("todo_cli")
        owner_id = todo.owner_id()
        root = Path(os.environ.get("TODO_APPROVAL_ROOT", "~/.hermes/todo-approvals")).expanduser()
        transport = __import__("todo_discord").TodoDiscordTransport(token)
        directory_type = _repo_module("approval_directory").DiscordChannelDirectory
        directory = directory_type(token, owner_id, transport.api, root / "approval-directory.json")
        lease = _repo_module("approval_lease").FileKeyLease(root / "approval-leases")
        run_once(
            store=TodoApprovalStore(root),
            owner_id=owner_id,
            transport=transport,
            directory=directory,
            approval_log=todo.approval_log(),
            lease=lease,
            now=datetime.now(UTC),
        )
    except Exception as error:  # noqa: BLE001 - final no-agent cron boundary
        print(f"todo-confirm-watch error: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
