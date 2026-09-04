"""Re-post live approval cards after a renderer change (owner request 2026-09-02).

The lifecycle façade re-posts only on a content change (a different action hash). A
card *format* change keeps the hash, so the old card is deleted here and its record
returns to ``planned``; the next tick renders it again in the thread it already has
(``effects_live.thread_candidates``). Only ``posted`` records qualify — a decision the
owner already gave is never thrown away, and a card that cannot be deleted keeps its
record untouched so no live card is ever orphaned from state.
"""

from __future__ import annotations

from dataclasses import replace

from automation.interop.approval_lifecycle import ApprovalRequest, ApprovalSurfaceError

from .approval_gate import DiscordTransportLike, PlaudApprovalGate
from .model import PlaudSyncRecord
from .store import PlaudSyncStore


def reset_for_repost(record: PlaudSyncRecord) -> PlaudSyncRecord | None:
    """The ``planned`` twin of a ``posted`` record; ``None`` for every other status."""
    if record.status != "posted" or record.message_id is None:
        return None
    return replace(record, status="planned", message_id=None)


def repost_posted(store: PlaudSyncStore, transport: DiscordTransportLike) -> tuple[str, ...]:
    """Delete every live card and return its record to ``planned``; the keys reset."""
    reset: list[str] = []
    for record in store.pending():
        planned = reset_for_repost(record)
        if planned is None or record.message_id is None:
            continue
        request = ApprovalRequest(
            record.recording_id,
            record.action_hash,
            record.message_id,
            record.channel_id,
            record.created_at,
        )
        try:
            PlaudApprovalGate(record, store, transport).delete(request)
        except ApprovalSurfaceError:
            continue
        store.clear_message_id(record.recording_id, record.action_hash, record.message_id)
        store.update(planned)
        reset.append(record.recording_id)
    return tuple(reset)
