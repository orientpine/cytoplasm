"""PII-safe audit helpers for entity preflight."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final, cast

from .contracts import JsonValue, PreflightDecision

DEFAULT_AUDIT_ROOT = "~/.hermes/entity-preflight/audit"
DEFAULT_OPERATIONAL_ROOT = "~/.hermes/entity-preflight/operational"
PRIVATE_AUDIT_RETENTION: Final = timedelta(days=30)
OPERATIONAL_RETENTION: Final = timedelta(days=180)
_ACTIVE_LOG: Final = "entity-preflight.jsonl"
_LOCK_FILE: Final = ".retention.lock"
_FILE_MODE: Final = 0o600
_DIRECTORY_MODE: Final = 0o700


def input_sha256(raw_text: str) -> str:
    return "sha256:" + hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def operational_event(decision: PreflightDecision) -> dict[str, JsonValue]:
    """Return the only representation allowed in general operational logs.

    Raw text, mention surfaces, relationship questions, normalized/display
    values, source references, resource ids, and candidate ids are omitted.
    """
    source_counts: dict[str, int] = {}
    for candidate in decision.candidates:
        source_counts[candidate.source.value] = source_counts.get(candidate.source.value, 0) + 1
    entity_types = sorted({entity.entity_kind.value for entity in decision.request.entities})
    return {
        "event": "entity_preflight_decision",
        "correlation_id": decision.audit.correlation_id,
        "policy_version": decision.audit.policy_version,
        "target_system": decision.request.target_system,
        "operation": decision.request.operation,
        "entity_count": len(decision.request.entities),
        "entity_types": cast(JsonValue, entity_types),
        "candidate_count": len(decision.candidates),
        "candidate_sources": cast(JsonValue, source_counts),
        "selected_count": len(decision.selected),
        "decision": decision.decision.value,
        "reason": decision.reason.value,
        "needs_confirmation": decision.needs_confirmation,
        "input_sha256": decision.audit.input_sha256,
    }


class PrivateJsonlAuditStore:
    """Append full sensitive records under a mode-700 root and mode-600 file."""

    def __init__(self, root: str | Path = DEFAULT_AUDIT_ROOT) -> None:
        self.root = Path(root).expanduser()

    def append(self, event: Mapping[str, JsonValue]) -> str:
        with _locked_root(self.root):
            path = self.root / _ACTIVE_LOG
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
            file_descriptor = os.open(path, flags, _FILE_MODE)
            os.fchmod(file_descriptor, _FILE_MODE)
            with os.fdopen(file_descriptor, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(event), ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return str(path)


class JsonlOperationalLog:
    """Durable general log for the redacted preflight events and quality records.

    It is a separate root from the sensitive store so that retention and access
    can differ, and it reuses the same hardened writer because these records are
    still personal-adjacent operational metadata.
    """

    def __init__(self, root: str | Path = DEFAULT_OPERATIONAL_ROOT) -> None:
        self._store = PrivateJsonlAuditStore(root)

    def emit(self, event: Mapping[str, JsonValue]) -> None:
        self._store.append(event)


def rotate_entity_preflight_logs(
    private_root: str | Path = DEFAULT_AUDIT_ROOT,
    operational_root: str | Path = DEFAULT_OPERATIONAL_ROOT,
    *,
    now: datetime | None = None,
) -> None:
    """Rotate both stores and enforce their PII-aware retention windows."""

    rotation_time = now or datetime.now(timezone.utc)
    _rotate_root(Path(private_root).expanduser(), PRIVATE_AUDIT_RETENTION, rotation_time)
    _rotate_root(Path(operational_root).expanduser(), OPERATIONAL_RETENTION, rotation_time)


@contextmanager
def _locked_root(root: Path) -> Iterator[None]:
    root.mkdir(mode=_DIRECTORY_MODE, parents=True, exist_ok=True)
    root.chmod(_DIRECTORY_MODE)
    descriptor = os.open(root / _LOCK_FILE, os.O_CREAT | os.O_RDWR, _FILE_MODE)
    os.fchmod(descriptor, _FILE_MODE)
    with os.fdopen(descriptor, "r+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _rotate_root(root: Path, retention: timedelta, now: datetime) -> None:
    with _locked_root(root):
        active = root / _ACTIVE_LOG
        if active.exists() and active.stat().st_size > 0:
            backup = _backup_path(root, now)
            os.replace(active, backup)
            backup.chmod(_FILE_MODE)
            descriptor = os.open(backup, os.O_RDONLY)
            with os.fdopen(descriptor, "rb") as backup_file:
                os.fsync(backup_file.fileno())
        descriptor = os.open(active, os.O_CREAT | os.O_WRONLY, _FILE_MODE)
        os.fchmod(descriptor, _FILE_MODE)
        with os.fdopen(descriptor, "wb") as active_file:
            active_file.flush()
            os.fsync(active_file.fileno())
        _prune_archives(root, now - retention)
        _fsync_directory(root)


def _backup_path(root: Path, now: datetime) -> Path:
    timestamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = root / f"entity-preflight.{timestamp}.jsonl"
    suffix = 2
    while candidate.exists():
        candidate = root / f"entity-preflight.{timestamp}-{suffix}.jsonl"
        suffix += 1
    return candidate


def _prune_archives(root: Path, cutoff: datetime) -> None:
    prefix_length = len("entity-preflight.")
    timestamp_length = len("20260803T000000Z")
    for path in root.glob("entity-preflight.*.jsonl"):
        if not stat.S_ISREG(path.lstat().st_mode):
            continue
        timestamp = path.name[prefix_length : prefix_length + timestamp_length]
        try:
            created_at = datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if created_at < cutoff:
            path.unlink()


def _fsync_directory(root: Path) -> None:
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
