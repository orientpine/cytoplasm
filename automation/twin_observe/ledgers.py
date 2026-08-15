from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, TypeAlias

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

_APPROVED_STATUSES: Final = frozenset({"approved", "executed", "saved", "sent"})
_REJECTED_STATUSES: Final = frozenset(
    {"approval_expired", "cancelled", "denied", "invalid", "owner_cancelled", "rejected"}
)


class Verdict(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class LedgerSource:
    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class GateEvent:
    skill: str
    action: str
    verdict: Verdict
    ts: str
    ledger: str


@dataclass(frozen=True, slots=True)
class LedgerReadResult:
    events: tuple[GateEvent, ...]
    skipped_lines: int
    unreadable_ledgers: tuple[str, ...]


def _nonempty_string(value: JsonValue | None) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _record_verdict(status: str) -> Verdict | None:
    if status in _APPROVED_STATUSES:
        return Verdict.APPROVE
    if status in _REJECTED_STATUSES:
        return Verdict.REJECT
    return None


def _valid_timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _parse_event(source: LedgerSource, value: JsonValue) -> GateEvent | None:
    if not isinstance(value, dict):
        return None
    action_value = _nonempty_string(value.get("action"))
    record_hash = _nonempty_string(value.get("hash"))
    target_id = _nonempty_string(value.get("target_id"))
    timestamp = _nonempty_string(value.get("timestamp"))
    approval = value.get("approval")
    result = value.get("result")
    if (
        action_value is None
        or record_hash is None
        or target_id is None
        or timestamp is None
        or not _valid_timestamp(timestamp)
        or not isinstance(approval, dict)
        or not isinstance(result, dict)
    ):
        return None
    channel = _nonempty_string(approval.get("channel"))
    method = _nonempty_string(approval.get("method"))
    reference = _nonempty_string(approval.get("ref")) or _nonempty_string(approval.get("message_id"))
    status = _nonempty_string(result.get("status"))
    skill, separator, action = action_value.partition(".")
    verdict = _record_verdict(status) if status is not None else None
    if not channel or not method or not reference or not separator or not action or verdict is None:
        return None
    return GateEvent(skill=skill, action=action, verdict=verdict, ts=timestamp, ledger=source.name)


def read_ledgers(sources: tuple[LedgerSource, ...]) -> LedgerReadResult:
    events: list[GateEvent] = []
    unreadable_ledgers: list[str] = []
    skipped_lines = 0
    for source in sources:
        try:
            lines = source.path.read_bytes().splitlines()
        except OSError:
            unreadable_ledgers.append(source.name)
            continue
        for raw_line in lines:
            try:
                decoded = raw_line.decode("utf-8")
                parsed: JsonValue = json.loads(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError):
                skipped_lines += 1
                continue
            event = _parse_event(source, parsed)
            if event is None:
                skipped_lines += 1
                continue
            events.append(event)
    return LedgerReadResult(tuple(events), skipped_lines, tuple(unreadable_ledgers))
