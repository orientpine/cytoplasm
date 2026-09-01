from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from automation.selfskill_audit.scan import SkillSnapshot
from automation.selfskill_audit.store import AuditError, JsonValue, _mapping


class Action(StrEnum):
    CREATED = "created"
    EDITED = "edited"
    ARCHIVED = "archived"
    RESTORED = "restored"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class Delta:
    action: Action
    name: str
    sha256: str
    provenance: str
    pinned: bool
    archived_at: str | None
    timestamp: str


def _delta(action: Action, snapshot: SkillSnapshot, timestamp: str) -> Delta:
    return Delta(action, snapshot.name, snapshot.sha256, snapshot.provenance, snapshot.pinned, snapshot.archived_at, timestamp)


def _diff(
    previous_active: tuple[SkillSnapshot, ...],
    previous_archived: tuple[SkillSnapshot, ...],
    active: tuple[SkillSnapshot, ...],
    archived: tuple[SkillSnapshot, ...],
    timestamp: str,
) -> tuple[Delta, ...]:
    old_active = {item.name: item for item in previous_active}
    old_archived = {item.name: item for item in previous_archived}
    current_active = {item.name: item for item in active}
    current_archived = {item.name: item for item in archived}
    if current_active.keys() & current_archived.keys():
        raise AuditError("a skill exists in both active and archive roots")
    deltas: list[Delta] = []
    for name, snapshot in current_active.items():
        if name in old_archived:
            deltas.append(_delta(Action.RESTORED, snapshot, timestamp))
        elif name not in old_active:
            deltas.append(_delta(Action.CREATED, snapshot, timestamp))
        elif snapshot.sha256 != old_active[name].sha256:
            deltas.append(_delta(Action.EDITED, snapshot, timestamp))
    for name, snapshot in current_archived.items():
        if name in old_active or name not in old_archived or snapshot.sha256 != old_archived[name].sha256:
            deltas.append(_delta(Action.ARCHIVED, snapshot, timestamp))
    vanished = (old_active.keys() | old_archived.keys()) - (current_active.keys() | current_archived.keys())
    for name in sorted(vanished):
        deltas.append(_delta(Action.REMOVED, old_active.get(name) or old_archived[name], timestamp))
    return tuple(deltas)


def _delta_payload(delta: Delta) -> dict[str, JsonValue]:
    return {
        "action": delta.action.value,
        "archived_at": delta.archived_at,
        "name": delta.name,
        "pinned": delta.pinned,
        "provenance": delta.provenance,
        "sha256": delta.sha256,
        "timestamp": delta.timestamp,
    }


def _parse_delta(value: JsonValue) -> Delta:
    record = _mapping(value, "audit ledger delta")
    try:
        action = Action(record.get("action"))
    except (TypeError, ValueError) as error:
        raise AuditError("audit ledger action is malformed") from error
    required = (record.get("name"), record.get("sha256"), record.get("provenance"), record.get("timestamp"))
    pinned, archived_at = record.get("pinned"), record.get("archived_at")
    if not all(isinstance(item, str) for item in required) or not isinstance(pinned, bool):
        raise AuditError("audit ledger delta is malformed")
    if archived_at is not None and not isinstance(archived_at, str):
        raise AuditError("audit ledger archive timestamp is malformed")
    name, sha256, provenance, timestamp = required
    assert isinstance(name, str) and isinstance(sha256, str) and isinstance(provenance, str) and isinstance(timestamp, str)
    return Delta(action, name, sha256, provenance, pinned, archived_at, timestamp)
