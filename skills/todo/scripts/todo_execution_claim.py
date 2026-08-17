from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from todo_approval_model import TodoApprovalRecord
from todo_approval_store import TodoApprovalStore, TodoApprovalStoreError


JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
_MODE = 0o600


class ApprovalClaimError(RuntimeError):
    pass


class ApprovalAlreadyConsumedError(ApprovalClaimError):
    pass


class ApprovalReconciliationRequiredError(ApprovalClaimError):
    pass


@dataclass(frozen=True, slots=True)
class ApprovalClaim:
    record: TodoApprovalRecord
    path: Path


@dataclass(frozen=True, slots=True)
class ApprovalClaimStore:
    root: Path

    def acquire(self, decision: Any, context: Any) -> ApprovalClaim:
        record = self._approved_record(decision.action_hash, decision.target_id, context)
        path = self._path(record)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = self._payload(record, "write_started", None)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _MODE)
        except FileExistsError:
            try:
                state = self.status(record)
            except ApprovalClaimError:
                state = "write_started"
            if state == "verified":
                raise ApprovalAlreadyConsumedError("todo approval was already consumed") from None
            raise ApprovalReconciliationRequiredError(
                "todo write already started; reconciliation is required"
            ) from None
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        return ApprovalClaim(record, path)

    def complete(self, claim: ApprovalClaim, task_id: str, title: str) -> None:
        if self.status(claim.record) != "write_started":
            raise ApprovalClaimError("todo claim is not write_started")
        payload = self._payload(
            claim.record,
            "verified",
            {"task_id": task_id, "title": title, "verification": "tasks.tasks.get"},
        )
        temporary = claim.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        temporary.chmod(_MODE)
        os.replace(temporary, claim.path)

    def status(self, record: TodoApprovalRecord) -> str | None:
        try:
            payload = json.loads(self._path(record).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as error:
            raise ApprovalClaimError("todo claim is unreadable") from error
        state = payload.get("state") if isinstance(payload, dict) else None
        if state not in {"write_started", "verified"}:
            raise ApprovalClaimError("todo claim state is invalid")
        return state

    def _approved_record(self, action_hash: str, target_id: str, context: Any) -> TodoApprovalRecord:
        approval_log = context.approval_log
        if approval_log is None:
            raise ApprovalClaimError("todo approval log is unavailable")
        message_ids = _approved_message_ids(approval_log, action_hash, target_id, context.owner_id)
        key = f"todo:{action_hash}"
        try:
            generations = tuple(
                record for record in TodoApprovalStore(self.root).archives(key)
                if record.action_hash == action_hash
                and record.target_id == target_id
            )
        except TodoApprovalStoreError as error:
            raise ApprovalClaimError(str(error)) from error
        if not generations:
            raise ApprovalClaimError("no archived approved todo generation matches the ledger")
        latest = max(generations, key=lambda record: record.generation)
        if latest.outcome != "approved" or latest.message_id not in message_ids:
            raise ApprovalClaimError("latest todo generation is not an unconsumed approval")
        return latest

    def _path(self, record: TodoApprovalRecord) -> Path:
        from automation.interop.approval_lease import slug

        return self.root / "claims" / slug(record.key) / f"{record.generation}.json"

    @staticmethod
    def _payload(record: TodoApprovalRecord, state: str, receipt: dict[str, str] | None) -> dict[str, JsonValue]:
        receipt_value: dict[str, JsonValue] | None = (
            None if receipt is None else {key: value for key, value in receipt.items()}
        )
        payload: dict[str, JsonValue] = {
            "action_hash": record.action_hash,
            "generation": record.generation,
            "key": record.key,
            "message_id": record.message_id,
            "receipt": receipt_value,
            "state": state,
            "target_id": record.target_id,
        }
        return payload


def _approved_message_ids(path: Path, action_hash: str, target_id: str, owner_id: str) -> frozenset[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ApprovalClaimError("todo approval log is unreadable") from error
    found: set[str] = set()
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        approval = row.get("approval") if isinstance(row, dict) else None
        if not isinstance(approval, dict):
            continue
        message_id = approval.get("message_id")
        if (
            row.get("action") == "external_effect.approval"
            and row.get("hash") == action_hash
            and row.get("target_id") == target_id
            and row.get("result") == {"status": "approved"}
            and approval.get("channel") == "approvals"
            and approval.get("method") == "manual_reaction"
            and approval.get("owner_id") == owner_id
            and isinstance(message_id, str)
        ):
            found.add(message_id)
    return frozenset(found)
