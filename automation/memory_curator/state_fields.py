"""Strict codecs for nested memory-curator state fields."""

from __future__ import annotations

from typing import Final, TypeGuard

from .model import MemoryKind
from .state_models import AlertState, PromotionRecord, PromotionStatus, StateError

_PROMOTION_KEYS: Final = frozenset(
    {
        "source_kind",
        "entry_sha256",
        "slug",
        "created_at",
        "note_sha256",
        "draft_id",
        "confirm_message_id",
        "status",
        "posted_at",
        "reconciled_at",
        "backup_path",
        "last_block_reason",
    },
)
_ALERT_KEYS: Final = frozenset(
    {
        "last_observed_signature",
        "last_sent_signature",
        "last_sent_at",
        "pending_signature",
    },
)


def _is_mapping(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def as_mapping(value: object, field: str) -> dict[object, object]:
    if not _is_mapping(value):
        raise StateError(f"curator state field {field!r} must be an object")
    return value


def _is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def as_list(value: object, field: str) -> list[object]:
    if not _is_list(value):
        raise StateError(f"curator state field {field!r} must be a list")
    return value


def as_string(value: object, field: str) -> str:
    match value:
        case str() as parsed:
            return parsed
        case _:
            raise StateError(f"curator state field {field!r} must be a string")


def as_optional_string(value: object, field: str) -> str | None:
    match value:
        case None:
            return None
        case str() as parsed:
            return parsed
        case _:
            raise StateError(f"curator state field {field!r} must be a string or null")


def as_optional_int(value: object, field: str) -> int | None:
    match value:
        case None:
            return None
        case int() as parsed if not isinstance(parsed, bool):
            return parsed
        case _:
            raise StateError(f"curator state field {field!r} must be an integer or null")


def _memory_kind(value: object) -> MemoryKind:
    match value:
        case "memory":
            return "memory"
        case "user":
            return "user"
        case _:
            raise StateError(f"unknown curator source_kind: {value!r}")


def _promotion_status(value: object) -> PromotionStatus:
    match value:
        case "prepared":
            return "prepared"
        case "posted":
            return "posted"
        case "reconciled":
            return "reconciled"
        case "legacy_unbound":
            return "legacy_unbound"
        case "abandoned":
            return "abandoned"
        case _:
            raise StateError(f"unknown curator promotion status: {value!r}")


def parse_promotion(raw: object) -> PromotionRecord:
    payload = as_mapping(raw, "promotion")
    if frozenset(payload) != _PROMOTION_KEYS:
        raise StateError("curator promotion record has an unknown shape")
    return PromotionRecord(
        source_kind=_memory_kind(payload["source_kind"]),
        entry_sha256=as_string(payload["entry_sha256"], "entry_sha256"),
        slug=as_string(payload["slug"], "slug"),
        created_at=as_string(payload["created_at"], "created_at"),
        note_sha256=as_string(payload["note_sha256"], "note_sha256"),
        draft_id=as_optional_string(payload["draft_id"], "draft_id"),
        confirm_message_id=as_optional_string(
            payload["confirm_message_id"], "confirm_message_id"
        ),
        status=_promotion_status(payload["status"]),
        posted_at=as_optional_string(payload["posted_at"], "posted_at"),
        reconciled_at=as_optional_string(payload["reconciled_at"], "reconciled_at"),
        backup_path=as_optional_string(payload["backup_path"], "backup_path"),
        last_block_reason=as_optional_string(
            payload["last_block_reason"], "last_block_reason"
        ),
    )


def parse_alert(raw: object) -> AlertState:
    payload = as_mapping(raw, "alert")
    if frozenset(payload) != _ALERT_KEYS:
        raise StateError("curator alert state has an unknown shape")
    return AlertState(
        last_observed_signature=as_optional_string(
            payload["last_observed_signature"], "last_observed_signature"
        ),
        last_sent_signature=as_optional_string(
            payload["last_sent_signature"], "last_sent_signature"
        ),
        last_sent_at=as_optional_string(payload["last_sent_at"], "last_sent_at"),
        pending_signature=as_optional_string(
            payload["pending_signature"], "pending_signature"
        ),
    )


def serialize_promotion(record: PromotionRecord) -> dict[str, object]:
    return {
        "source_kind": record.source_kind,
        "entry_sha256": record.entry_sha256,
        "slug": record.slug,
        "created_at": record.created_at,
        "note_sha256": record.note_sha256,
        "draft_id": record.draft_id,
        "confirm_message_id": record.confirm_message_id,
        "status": record.status,
        "posted_at": record.posted_at,
        "reconciled_at": record.reconciled_at,
        "backup_path": record.backup_path,
        "last_block_reason": record.last_block_reason,
    }


def serialize_alert(alert: AlertState) -> dict[str, object]:
    return {
        "last_observed_signature": alert.last_observed_signature,
        "last_sent_signature": alert.last_sent_signature,
        "last_sent_at": alert.last_sent_at,
        "pending_signature": alert.pending_signature,
    }
