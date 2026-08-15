"""Ops-owned durable repair lifecycle, independent of the Kanban dispatcher."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, TypeAlias, cast

from automation.repair.repair_redaction import redact


MAX_SANDBOX_CHECKS: Final = 960


JsonValue: TypeAlias = "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"


class LifecycleState(StrEnum):
    OPEN = "open"
    DIAGNOSING = "diagnosing"
    SANDBOXED = "sandboxed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLIED = "applied"
    DONE = "done"
    REOPENED = "reopened"


@dataclass(frozen=True, slots=True)
class LifecycleRecord:
    ticket_id: str
    state: LifecycleState
    reason: str
    updated_at: str
    sandbox_checks: str = ""


@dataclass(frozen=True, slots=True)
class RepairLifecycleStore:
    """Persist only redacted lifecycle data in an ops-private directory."""

    root: Path
    mode: Final[int] = 0o700

    def transition(self, ticket_id: str, state: LifecycleState, reason: str = "") -> LifecycleRecord:
        """Atomically record one safe lifecycle transition before a mirror update."""
        self.root.mkdir(mode=self.mode, parents=True, exist_ok=True)
        _ = self.root.chmod(self.mode)
        target = self.root / f"{ticket_id}.json"
        existing_checks = self.read(ticket_id).sandbox_checks if target.is_file() else ""
        sandbox_checks = redact(reason)[:MAX_SANDBOX_CHECKS] if state is LifecycleState.SANDBOXED else existing_checks
        record = LifecycleRecord(ticket_id, state, redact(reason)[:240], datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), sandbox_checks)
        temporary = target.with_suffix(".tmp")
        _ = temporary.write_text(json.dumps(asdict(record), separators=(",", ":")), encoding="utf-8")
        _ = temporary.chmod(0o600)
        os.replace(temporary, target)
        return record

    def read(self, ticket_id: str) -> LifecycleRecord:
        """Return the current lifecycle record for a repair ticket."""
        payload = _string_mapping(cast(JsonValue, json.loads((self.root / f"{ticket_id}.json").read_text(encoding="utf-8"))))
        return LifecycleRecord(
            ticket_id,
            LifecycleState(_required(payload, "state")),
            _required(payload, "reason"),
            _required(payload, "updated_at"),
            payload.get("sandbox_checks", ""),
        )


def _string_mapping(raw: JsonValue) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("repair lifecycle record must be an object")
    parsed: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            raise ValueError("repair lifecycle record fields must be strings")
        parsed[key] = value
    return parsed


def _required(payload: dict[str, str], key: str) -> str:
    value = payload.get(key)
    if value is None:
        raise ValueError(f"repair lifecycle record missing {key}")
    return value
