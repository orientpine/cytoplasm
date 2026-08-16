from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol, TypeAlias

from automation.skill_review import skill_digest

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
_SKILL_NAME: Final = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
_STATE_VERSION: Final = 1


class JsonLoader(Protocol):
    def __call__(self, document: str) -> JsonValue: ...


_JSON_LOADS: JsonLoader = json.loads


class AuditError(RuntimeError):
    pass


class Action(StrEnum):
    CREATED = "created"
    EDITED = "edited"
    ARCHIVED = "archived"
    RESTORED = "restored"


@dataclass(frozen=True, slots=True)
class SkillSnapshot:
    name: str
    sha256: str
    provenance: str
    pinned: bool
    archived_at: str | None


@dataclass(frozen=True, slots=True)
class Delta:
    action: Action
    name: str
    sha256: str
    provenance: str
    pinned: bool
    archived_at: str | None
    timestamp: str


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


def _mapping(value: JsonValue, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise AuditError(f"{label} must be a JSON object")
    return value


def _read_json(path: Path, label: str) -> JsonValue:
    try:
        document = path.read_text(encoding="utf-8")
        return _JSON_LOADS(document)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuditError(f"cannot read trusted {label}: {path}") from error


def _usage(skills_root: Path) -> dict[str, JsonValue]:
    # 부재는 정상이다 — 반전 직후의 새 1차 루트에는 Hermes가 아직 상태를 쓰지 않았다.
    # 손상은 정상이 아니다: 읽히지 않거나 형태가 틀리면 그대로 실패한다(fail-closed).
    usage_path = skills_root / ".usage.json"
    if not usage_path.exists():
        return {}
    raw = _mapping(_read_json(usage_path, "skill usage"), "skill usage")
    nested = raw.get("skills")
    return _mapping(nested, "skill usage.skills") if nested is not None else raw


def _snapshot(skill_dir: Path, usage: dict[str, JsonValue]) -> SkillSnapshot:
    name = skill_dir.name
    if _SKILL_NAME.fullmatch(name) is None:
        raise AuditError(f"unsafe skill name: {name!r}")
    raw_record = usage.get(name, {})
    record = _mapping(raw_record, f"usage record for {name}")
    created_by = record.get("created_by")
    agent_created = record.get("agent_created")
    pinned = record.get("pinned", False)
    archived_at = record.get("archived_at")
    if created_by is not None and not isinstance(created_by, str):
        raise AuditError(f"created_by is malformed for {name}")
    if agent_created is not None and not isinstance(agent_created, bool):
        raise AuditError(f"agent_created is malformed for {name}")
    if not isinstance(pinned, bool):
        raise AuditError(f"pinned is malformed for {name}")
    if archived_at is not None and not isinstance(archived_at, str):
        raise AuditError(f"archived_at is malformed for {name}")
    provenance = "agent" if created_by == "agent" or agent_created is True else "unverified"
    try:
        digest = skill_digest(skill_dir)
    except OSError as error:
        raise AuditError(f"cannot hash skill: {name}") from error
    return SkillSnapshot(name, digest, provenance, pinned, archived_at)


def _children(root: Path) -> tuple[Path, ...]:
    try:
        return tuple(sorted(root.iterdir(), key=lambda path: os.fsencode(path.name)))
    except OSError as error:
        raise AuditError(f"cannot scan skills root: {root}") from error


def _is_skill_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink() and (path / "SKILL.md").is_file()


def _scan(root: Path, usage: dict[str, JsonValue]) -> tuple[SkillSnapshot, ...]:
    # Hermes는 자가 저작 스킬을 카테고리 디렉터리 아래에 둔다 — peer 실측
    # `software-development/<name>/SKILL.md`. 최상위만 훑으면 원장이 영구히 0건을 보고한다.
    # 그래서 깊이 1과 2를 함께 훑되, `.archive`/`.hub` 같은 Hermes 자체 상태는 건너뛴다
    # (아카이브는 호출자가 루트로 직접 넘겨주므로 그때는 그 자식들이 대상이 된다).
    found: list[SkillSnapshot] = []
    for path in _children(root):
        if path.name.startswith("."):
            continue
        if _is_skill_dir(path):
            found.append(_snapshot(path, usage))
            continue
        if path.is_dir() and not path.is_symlink():
            for nested in _children(path):
                if _is_skill_dir(nested):
                    found.append(_snapshot(nested, usage))
    return tuple(found)


def _parse_snapshot(value: JsonValue, name: str) -> SkillSnapshot:
    record = _mapping(value, f"stored snapshot for {name}")
    sha256 = record.get("sha256")
    provenance = record.get("provenance")
    pinned = record.get("pinned")
    archived_at = record.get("archived_at")
    if not isinstance(sha256, str) or not isinstance(provenance, str) or not isinstance(pinned, bool):
        raise AuditError(f"stored snapshot is malformed for {name}")
    if archived_at is not None and not isinstance(archived_at, str):
        raise AuditError(f"stored archive timestamp is malformed for {name}")
    return SkillSnapshot(name, sha256, provenance, pinned, archived_at)


def _load_state(path: Path) -> AuditState:
    if not path.exists():
        return AuditState((), (), 0)
    raw = _mapping(_read_json(path, "audit state"), "audit state")
    if raw.get("version") != _STATE_VERSION or not isinstance(raw.get("reported_lines"), int):
        raise AuditError("audit state schema is unsupported")
    active = _mapping(raw.get("active"), "audit state.active")
    archived = _mapping(raw.get("archived"), "audit state.archived")
    return AuditState(
        tuple(_parse_snapshot(value, name) for name, value in sorted(active.items())),
        tuple(_parse_snapshot(value, name) for name, value in sorted(archived.items())),
        raw["reported_lines"],
    )


def _delta(action: Action, snapshot: SkillSnapshot, timestamp: str) -> Delta:
    return Delta(action, snapshot.name, snapshot.sha256, snapshot.provenance, snapshot.pinned, snapshot.archived_at, timestamp)


def _diff(previous: AuditState, active: tuple[SkillSnapshot, ...], archived: tuple[SkillSnapshot, ...], timestamp: str) -> tuple[Delta, ...]:
    old_active = {item.name: item for item in previous.active}
    old_archived = {item.name: item for item in previous.archived}
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


def _state_payload(state: AuditState) -> dict[str, JsonValue]:
    def snapshots(items: tuple[SkillSnapshot, ...]) -> dict[str, JsonValue]:
        return {item.name: {"sha256": item.sha256, "provenance": item.provenance, "pinned": item.pinned, "archived_at": item.archived_at} for item in items}

    return {"version": _STATE_VERSION, "active": snapshots(state.active), "archived": snapshots(state.archived), "reported_lines": state.reported_lines}


def _atomic_write(path: Path, document: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary_path = Path(handle.name)
            _ = handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as error:
        raise AuditError(f"cannot atomically write private audit file: {path}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _parse_ledger(path: Path) -> tuple[Delta, ...]:
    if not path.exists():
        return ()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        return tuple(_parse_delta(_JSON_LOADS(line)) for line in lines)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuditError(f"audit ledger is unreadable: {path}") from error


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


def audit(home: Path, *, now: datetime) -> AuditResult:
    if now.tzinfo is None:
        raise AuditError("audit timestamp must be timezone-aware")
    skills_root = home / ".hermes" / "skills"
    audit_root = home / ".hermes" / "selfskill-audit"
    if any((parent / ".git").exists() for parent in (audit_root, *audit_root.parents)):
        raise AuditError("audit state path must live outside a git checkout")
    state_path, ledger_path = audit_root / "state.json", audit_root / "ledger.jsonl"
    previous = _load_state(state_path)
    usage = _usage(skills_root)
    active = _scan(skills_root, usage)
    archive_root = skills_root / ".archive"
    archived = _scan(archive_root, usage) if archive_root.exists() else ()
    timestamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    deltas = _diff(previous, active, archived, timestamp)
    ledger = (*_parse_ledger(ledger_path), *deltas)
    if previous.reported_lines < 0 or previous.reported_lines > len(ledger):
        raise AuditError("audit report watermark is outside the ledger")
    _atomic_write(ledger_path, "".join(json.dumps(_delta_payload(item), ensure_ascii=False, sort_keys=True) + "\n" for item in ledger))
    _atomic_write(state_path, json.dumps(_state_payload(AuditState(active, archived, previous.reported_lines)), ensure_ascii=False, sort_keys=True) + "\n")
    return AuditResult(deltas, ledger[previous.reported_lines:], state_path, ledger_path, len(ledger))


def mark_reported(result: AuditResult) -> None:
    state = _load_state(result.state_path)
    _atomic_write(result.state_path, json.dumps(_state_payload(replace(state, reported_lines=result.ledger_lines)), ensure_ascii=False, sort_keys=True) + "\n")
