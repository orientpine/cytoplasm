from __future__ import annotations

from enum import Enum
from typing import Protocol, cast, runtime_checkable

from automation.hermes_compat.owner_dm_relatedness import Relatedness
from automation.hermes_compat.receipt_tracker import (
    RECEIPT_MEMBERS_KEY,
    RECEIPT_MESSAGE_IDS_KEY,
)


class RouteOutcome(Enum):
    MERGED_TAIL = "merged_tail"
    APPENDED = "appended"
    REJECTED_OVER_CAP = "rejected_over_cap"


@runtime_checkable
class _TextEvent(Protocol):
    text: str
    metadata: dict[str, object]


@runtime_checkable
class _MediaEvent(Protocol):
    media_urls: list[object]


def queue_depth(
    pending_slot: dict[str, object],
    overflow: dict[str, list[object]],
    session_key: str,
) -> int:
    return (1 if session_key in pending_slot else 0) + len(overflow.get(session_key, []))


def route(
    pending_slot: dict[str, object],
    overflow: dict[str, list[object]],
    session_key: str,
    event: object,
    relatedness: Relatedness,
    *,
    cap: int,
) -> RouteOutcome:
    if relatedness is Relatedness.MERGE_TAIL:
        queued = overflow.get(session_key, [])
        tail = queued[-1] if queued else pending_slot.get(session_key)
        if isinstance(tail, _TextEvent) and isinstance(event, _TextEvent):
            # Validate + compute EVERYTHING before mutating: a raise after a
            # partial mutation would let the caller fall back to base-debounce
            # and double-process an already-changed tail.
            merged_text = f"{tail.text}\n{event.text}" if tail.text else event.text
            merged_ids = _merged_receipts(tail.metadata, event.metadata, RECEIPT_MESSAGE_IDS_KEY)
            merged_members = _merged_receipts(tail.metadata, event.metadata, RECEIPT_MEMBERS_KEY)
            incoming_urls = list(event.media_urls) if isinstance(event, _MediaEvent) else []
            # Commit atomically (no raise past this point).
            tail.text = merged_text
            tail.metadata[RECEIPT_MESSAGE_IDS_KEY] = merged_ids
            tail.metadata[RECEIPT_MEMBERS_KEY] = merged_members
            if incoming_urls and isinstance(tail, _MediaEvent):
                tail.media_urls.extend(incoming_urls)
            return RouteOutcome.MERGED_TAIL

    if queue_depth(pending_slot, overflow, session_key) >= cap:
        return RouteOutcome.REJECTED_OVER_CAP
    if session_key in pending_slot:
        overflow.setdefault(session_key, []).append(event)
    else:
        pending_slot[session_key] = event
    return RouteOutcome.APPENDED


def prepend(
    pending_slot: dict[str, object],
    overflow: dict[str, list[object]],
    session_key: str,
    event: object,
) -> None:
    old_head = pending_slot.get(session_key)
    if old_head is not None:
        overflow.setdefault(session_key, []).insert(0, old_head)
    pending_slot[session_key] = event


def _merged_receipts(
    target: dict[str, object],
    source: dict[str, object],
    key: str,
) -> list[object]:
    existing = target.get(key)
    incoming = source.get(key)
    base = cast("list[object]", existing) if isinstance(existing, list) else []
    add = cast("list[object]", incoming) if isinstance(incoming, list) else []
    return [*base, *add]
