"""Plaud lifelog sync — persisted record shapes with fail-closed parsing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, TypeGuard

PlaudStatus = Literal["transcribing", "planned", "posted", "approved", "written", "abandoned"]

_STATUSES: Final = frozenset(
    {"transcribing", "planned", "posted", "approved", "written", "abandoned"}
)
_VERSION: Final = 1
_STATE_KEYS: Final = frozenset({"version", "last_poll_at", "records"})
_RECORD_KEYS: Final = frozenset(
    {
        "version",
        "recording_id",
        "recorded_at",
        "note_relpath",
        "note_title",
        "body_sha256",
        "action_hash",
        "status",
        "kind",
        "surface",
        "channel_id",
        "policy_version",
        "message_id",
        "created_at",
        "approved_at",
        "written_at",
        "remote_ref",
        "note_content_sha256",
        "last_block_reason",
    }
)
_OPTIONAL_RECORD_KEYS: Final = frozenset({"approval_thread_id", "transcribe_attempts"})


class PlaudSyncError(ValueError):
    """Persisted plaud-sync state could not be parsed without guessing."""


@dataclass(frozen=True, slots=True)
class PlaudSyncRecord:
    version: int
    recording_id: str
    recorded_at: str
    note_relpath: str
    note_title: str
    body_sha256: str
    action_hash: str
    status: PlaudStatus
    kind: str
    surface: str
    channel_id: str
    policy_version: int
    message_id: str | None
    created_at: str
    approved_at: str | None
    written_at: str | None
    remote_ref: str | None
    note_content_sha256: str | None
    last_block_reason: str | None
    approval_thread_id: str | None = None
    transcribe_attempts: int = 0


@dataclass(frozen=True, slots=True)
class PlaudSyncState:
    version: int
    last_poll_at: str | None
    records: Mapping[str, PlaudSyncRecord]

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", MappingProxyType(dict(self.records)))


def empty_state() -> PlaudSyncState:
    return PlaudSyncState(version=_VERSION, last_poll_at=None, records={})


def _is_mapping(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _mapping(value: object, field: str) -> dict[object, object]:
    if not _is_mapping(value):
        raise PlaudSyncError(f"plaud-sync state field {field!r} must be an object")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise PlaudSyncError(f"plaud-sync state field {field!r} must be a string")
    return value


def _string_or_none(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field)


def _integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PlaudSyncError(f"plaud-sync state field {field!r} must be an integer")
    return value


def _is_status(value: str) -> TypeGuard[PlaudStatus]:
    return value in _STATUSES


def _status(value: object) -> PlaudStatus:
    parsed = _string(value, "status")
    if not _is_status(parsed):
        raise PlaudSyncError(f"plaud-sync record status {parsed!r} is unknown")
    return parsed


def parse_record(raw: object) -> PlaudSyncRecord:
    payload = _mapping(raw, "record")
    keys = {key for key in payload if isinstance(key, str)}
    if len(keys) != len(payload):
        raise PlaudSyncError("plaud-sync record has a non-string key")
    missing = _RECORD_KEYS - keys
    unknown = keys - _RECORD_KEYS - _OPTIONAL_RECORD_KEYS
    if missing or unknown:
        raise PlaudSyncError(
            f"plaud-sync record shape mismatch: missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    data = {str(key): value for key, value in payload.items()}
    return PlaudSyncRecord(
        version=_integer(data["version"], "version"),
        recording_id=_string(data["recording_id"], "recording_id"),
        recorded_at=_string(data["recorded_at"], "recorded_at"),
        note_relpath=_string(data["note_relpath"], "note_relpath"),
        note_title=_string(data["note_title"], "note_title"),
        body_sha256=_string(data["body_sha256"], "body_sha256"),
        action_hash=_string(data["action_hash"], "action_hash"),
        status=_status(data["status"]),
        kind=_string(data["kind"], "kind"),
        surface=_string(data["surface"], "surface"),
        channel_id=_string(data["channel_id"], "channel_id"),
        policy_version=_integer(data["policy_version"], "policy_version"),
        message_id=_string_or_none(data["message_id"], "message_id"),
        created_at=_string(data["created_at"], "created_at"),
        approved_at=_string_or_none(data["approved_at"], "approved_at"),
        written_at=_string_or_none(data["written_at"], "written_at"),
        remote_ref=_string_or_none(data["remote_ref"], "remote_ref"),
        note_content_sha256=_string_or_none(data["note_content_sha256"], "note_content_sha256"),
        last_block_reason=_string_or_none(data["last_block_reason"], "last_block_reason"),
        approval_thread_id=_string_or_none(
            data.get("approval_thread_id"), "approval_thread_id"
        ),
        transcribe_attempts=_integer(data.get("transcribe_attempts", 0), "transcribe_attempts"),
    )


def parse_state(raw: object) -> PlaudSyncState:
    payload = _mapping(raw, "state")
    keys = {key for key in payload if isinstance(key, str)}
    if keys != _STATE_KEYS or len(keys) != len(payload):
        raise PlaudSyncError("plaud-sync state has an unexpected shape")
    data = {str(key): value for key, value in payload.items()}
    records_raw = _mapping(data["records"], "records")
    records: dict[str, PlaudSyncRecord] = {}
    for key, value in records_raw.items():
        records[_string(key, "records key")] = parse_record(value)
    return PlaudSyncState(
        version=_integer(data["version"], "version"),
        last_poll_at=_string_or_none(data["last_poll_at"], "last_poll_at"),
        records=records,
    )


def serialize_record(record: PlaudSyncRecord) -> dict[str, object]:
    row: dict[str, object] = {
        "version": record.version,
        "recording_id": record.recording_id,
        "recorded_at": record.recorded_at,
        "note_relpath": record.note_relpath,
        "note_title": record.note_title,
        "body_sha256": record.body_sha256,
        "action_hash": record.action_hash,
        "status": record.status,
        "kind": record.kind,
        "surface": record.surface,
        "channel_id": record.channel_id,
        "policy_version": record.policy_version,
        "message_id": record.message_id,
        "created_at": record.created_at,
        "approved_at": record.approved_at,
        "written_at": record.written_at,
        "remote_ref": record.remote_ref,
        "note_content_sha256": record.note_content_sha256,
        "last_block_reason": record.last_block_reason,
    }
    if record.approval_thread_id is not None:
        row["approval_thread_id"] = record.approval_thread_id
    if record.transcribe_attempts:
        row["transcribe_attempts"] = record.transcribe_attempts
    return row


def serialize_state(state: PlaudSyncState) -> dict[str, object]:
    return {
        "version": state.version,
        "last_poll_at": state.last_poll_at,
        "records": {key: serialize_record(record) for key, record in state.records.items()},
    }
