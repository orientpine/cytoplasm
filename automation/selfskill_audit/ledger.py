from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation.selfskill_audit.delta import Action, Delta, _delta_payload, _diff, _parse_delta
from automation.selfskill_audit.scan import (
    SkillSnapshot,
    _bundled_names,
    _governed_names,
    _parse_snapshot,
    _scan,
    _usage,
)
from automation.selfskill_audit.store import (
    AuditError,
    JsonValue,
    _JSON_LOADS,
    _atomic_write,
    _mapping,
    _read_json,
)
from automation.skill_review import skill_digest

_STATE_VERSION: Final = 1

__all__ = [
    "Action",
    "AuditResult",
    "AuditState",
    "Delta",
    "SkillSnapshot",
    "audit",
    "mark_reported",
    "skill_digest",
]


@dataclass(frozen=True, slots=True)
class AuditState:
    active: tuple[SkillSnapshot, ...]
    archived: tuple[SkillSnapshot, ...]
    reported_lines: int


@dataclass(frozen=True, slots=True)
class AuditResult:
    deltas: tuple[Delta, ...]
    pending_deltas: tuple[Delta, ...]
    state_path: Path
    ledger_path: Path
    ledger_lines: int
    shadowed: tuple[str, ...] = ()


def _load_state(path: Path) -> AuditState:
    if not path.exists():
        return AuditState((), (), 0)
    raw = _mapping(_read_json(path, "audit state"), "audit state")
    reported_lines = raw.get("reported_lines")
    if raw.get("version") != _STATE_VERSION or not isinstance(reported_lines, int):
        raise AuditError("audit state schema is unsupported")
    active = _mapping(raw.get("active"), "audit state.active")
    archived = _mapping(raw.get("archived"), "audit state.archived")
    return AuditState(
        tuple(_parse_snapshot(value, name) for name, value in sorted(active.items())),
        tuple(_parse_snapshot(value, name) for name, value in sorted(archived.items())),
        reported_lines,
    )


def _state_payload(state: AuditState) -> dict[str, JsonValue]:
    def snapshots(items: tuple[SkillSnapshot, ...]) -> dict[str, JsonValue]:
        return {
            item.name: {
                "sha256": item.sha256,
                "provenance": item.provenance,
                "pinned": item.pinned,
                "archived_at": item.archived_at,
            }
            for item in items
        }

    return {
        "version": _STATE_VERSION,
        "active": snapshots(state.active),
        "archived": snapshots(state.archived),
        "reported_lines": state.reported_lines,
    }


def _parse_ledger(path: Path) -> tuple[Delta, ...]:
    if not path.exists():
        return ()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        return tuple(_parse_delta(_JSON_LOADS(line)) for line in lines)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuditError(f"audit ledger is unreadable: {path}") from error


def audit(home: Path, *, now: datetime, governed_root: Path | None = None) -> AuditResult:
    if now.tzinfo is None:
        raise AuditError("audit timestamp must be timezone-aware")
    skills_root = home / ".hermes" / "skills"
    audit_root = home / ".hermes" / "selfskill-audit"
    if any((parent / ".git").exists() for parent in (audit_root, *audit_root.parents)):
        raise AuditError("audit state path must live outside a git checkout")
    state_path, ledger_path = audit_root / "state.json", audit_root / "ledger.jsonl"
    previous = _load_state(state_path)
    usage = _usage(skills_root)
    bundled = _bundled_names(skills_root)
    active = _scan(skills_root, usage, bundled)
    archive_root = skills_root / ".archive"
    archived = _scan(archive_root, usage, bundled) if archive_root.exists() else ()
    timestamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    deltas = _diff(previous.active, previous.archived, active, archived, timestamp)
    ledger = (*_parse_ledger(ledger_path), *deltas)
    if previous.reported_lines < 0 or previous.reported_lines > len(ledger):
        raise AuditError("audit report watermark is outside the ledger")
    _atomic_write(
        ledger_path,
        "".join(json.dumps(_delta_payload(item), ensure_ascii=False, sort_keys=True) + "\n" for item in ledger),
    )
    _atomic_write(
        state_path,
        json.dumps(_state_payload(AuditState(active, archived, previous.reported_lines)), ensure_ascii=False, sort_keys=True)
        + "\n",
    )
    governed = _governed_names(governed_root)
    shadowed = tuple(sorted(snapshot.name for snapshot in active if snapshot.name in governed))
    return AuditResult(deltas, ledger[previous.reported_lines:], state_path, ledger_path, len(ledger), shadowed)


def mark_reported(result: AuditResult) -> None:
    state = _load_state(result.state_path)
    _atomic_write(
        result.state_path,
        json.dumps(
            _state_payload(replace(state, reported_lines=result.ledger_lines)),
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
    )
