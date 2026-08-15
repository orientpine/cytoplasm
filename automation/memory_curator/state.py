"""Pure, versioned curator state with fail-closed v1 migration.

Migrated ``legacy_unbound`` records preserve audit history only. Their source
kind was not stored by v1, so they must never authorize source-entry deletion.
"""

from __future__ import annotations

from typing import Final

from .state_fields import (
    as_list,
    as_mapping,
    as_optional_int,
    as_optional_string,
    as_string,
    parse_alert,
    parse_promotion,
    serialize_alert,
    serialize_promotion,
)
from .state_models import (
    AlertState,
    CuratorState,
    PendingOwnerEvent,
    PendingOwnerPhase,
    PromotionRecord,
    PromotionStatus,
    StateError,
)

__all__ = (
    "AlertState",
    "CuratorState",
    "PendingOwnerEvent",
    "PromotionRecord",
    "PromotionStatus",
    "StateError",
    "empty_state",
    "parse_state",
    "serialize_state",
)

_VERSION: Final = 3
_V1_KEYS: Final = frozenset({"proposed"})
_V2_KEYS: Final = frozenset({"version", "promotions", "alert"})
_V3_KEYS: Final = frozenset(
    {"version", "promotions", "alert", "pending_owner_events"}
)
_PENDING_EVENT_KEYS: Final = frozenset(
    {"phase", "preview", "twin_kind", "draft_id", "freed_chars"}
)


def empty_state() -> CuratorState:
    return CuratorState(
        version=_VERSION,
        promotions={},
        alert=AlertState(None, None, None, None),
        pending_owner_events={},
    )


def _legacy_record(old_hash: str) -> PromotionRecord:
    return PromotionRecord(
        source_kind="memory",
        entry_sha256=old_hash,
        slug="",
        created_at="",
        note_sha256="",
        draft_id=None,
        confirm_message_id=None,
        status="legacy_unbound",
        posted_at=None,
        reconciled_at=None,
        backup_path=None,
        last_block_reason=None,
    )


def _migrate_v1(proposed: list[object]) -> CuratorState:
    promotions: dict[str, PromotionRecord] = {}
    for value in proposed:
        old_hash = as_string(value, "proposed[]")
        if old_hash in promotions:
            raise StateError(f"duplicate legacy curator hash: {old_hash!r}")
        promotions[old_hash] = _legacy_record(old_hash)
    return CuratorState(
        _VERSION,
        promotions,
        AlertState(None, None, None, None),
        {},
    )


def _parse_pending_status(value: object) -> PendingOwnerPhase:
    match value:
        case "posted":
            return "posted"
        case "deleted":
            return "deleted"
        case _:
            raise StateError(f"unknown pending owner event phase: {value!r}")


def _parse_pending_events(raw: object) -> dict[str, PendingOwnerEvent]:
    payload = as_mapping(raw, "pending_owner_events")
    events: dict[str, PendingOwnerEvent] = {}
    for raw_key, raw_event in payload.items():
        key = as_string(raw_key, "pending_owner_events key")
        event_payload = as_mapping(raw_event, "pending_owner_event")
        if frozenset(event_payload) != _PENDING_EVENT_KEYS:
            raise StateError("pending owner event has an unknown shape")
        phase = _parse_pending_status(event_payload["phase"])
        twin_kind = as_optional_string(event_payload["twin_kind"], "twin_kind")
        draft_id = as_optional_string(event_payload["draft_id"], "draft_id")
        freed_chars = as_optional_int(event_payload["freed_chars"], "freed_chars")
        if phase == "posted":
            if twin_kind is None or draft_id is None or freed_chars is not None:
                raise StateError("posted owner event fields are invalid")
        elif twin_kind is not None or draft_id is not None or freed_chars is None:
            raise StateError("deleted owner event fields are invalid")
        if not key.endswith(f"#{phase}"):
            raise StateError("pending owner event key does not match its phase")
        events[key] = PendingOwnerEvent(
            key=key,
            phase=phase,
            preview=as_string(event_payload["preview"], "preview"),
            twin_kind=twin_kind,
            draft_id=draft_id,
            freed_chars=freed_chars,
        )
    return events


def _parse_current(payload: dict[object, object], pending_raw: object) -> CuratorState:
    promotions: dict[str, PromotionRecord] = {}
    promotions_raw = as_mapping(payload["promotions"], "promotions")
    for raw_key, raw_record in promotions_raw.items():
        promotion_key = as_string(raw_key, "promotions key")
        promotions[promotion_key] = parse_promotion(raw_record)
    return CuratorState(
        _VERSION,
        promotions,
        parse_alert(payload["alert"]),
        _parse_pending_events(pending_raw),
    )


def _require_version(value: object, expected: int) -> None:
    match value:
        case int() as parsed if not isinstance(parsed, bool) and parsed == expected:
            return
        case _:
            raise StateError(f"unsupported curator state version: {value!r}")


def parse_state(raw: object) -> CuratorState:
    payload = as_mapping(raw, "root")
    keys = frozenset(payload)
    match keys == _V1_KEYS, keys == _V2_KEYS, keys == _V3_KEYS:
        case True, False, False:
            return _migrate_v1(as_list(payload["proposed"], "proposed"))
        case False, True, False:
            _require_version(payload["version"], 2)
            return _parse_current(payload, {})
        case False, False, True:
            _require_version(payload["version"], _VERSION)
            return _parse_current(payload, payload["pending_owner_events"])
        case _:
            raise StateError("curator state has an unknown top-level shape")


def _serialize_pending_event(event: PendingOwnerEvent) -> dict[str, object]:
    return {
        "phase": event.phase,
        "preview": event.preview,
        "twin_kind": event.twin_kind,
        "draft_id": event.draft_id,
        "freed_chars": event.freed_chars,
    }


def serialize_state(s: CuratorState) -> dict[str, object]:
    return {
        "version": _VERSION,
        "promotions": {
            key: serialize_promotion(record) for key, record in s.promotions.items()
        },
        "alert": serialize_alert(s.alert),
        "pending_owner_events": {
            key: _serialize_pending_event(event)
            for key, event in s.pending_owner_events.items()
        },
    }
