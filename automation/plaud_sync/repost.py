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
from typing import Final

from automation.interop.approval_lifecycle import ApprovalRequest, ApprovalSurfaceError

from .approval_gate import DiscordTransportLike, PlaudApprovalGate
from .model import PlaudSyncRecord
from .store import PlaudSyncStore, load_state


#: 녹음이 영원히 멈추는 두 자리. 틱도 store.pending() 도 여기를 다시 보지 않는다.
_TERMINAL: Final = frozenset({"written", "abandoned"})


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


def reset_for_reprocess(record: PlaudSyncRecord) -> PlaudSyncRecord | None:
    """The ``transcribing`` twin of a finished record; ``None`` while one is still live.

    ``written`` and ``abandoned`` are where a recording stops for good — the tick skips
    both and ``store.pending()`` does not list them. That is right for the ordinary case
    and wrong for the one the owner hit (2026-09-06): a note whose transcript was split
    into syllables and whose summary came back empty was produced by code we have since
    fixed, and nothing could rebuild it. Sending the record back to ``transcribing``
    re-runs download → transcription → extraction → card, and the record retains its
    first ``note_relpath`` so the approved write **upserts the same file**.

    The attempt counters go back to zero because they counted the old code's failures.
    ``approval_thread_id`` stays so the new card lands in the thread the owner already
    has, and ``action_hash`` is left alone — the freshly rendered body sets it, and that
    new hash is what makes this a new decision rather than a reused ✅.
    """
    if record.status not in _TERMINAL:
        return None
    return replace(
        record,
        status="transcribing",
        message_id=None,
        approved_at=None,
        written_at=None,
        last_block_reason=None,
        transcribe_attempts=0,
        next_transcribe_at=None,
    )


def reprocess(store: PlaudSyncStore, recording_id: str) -> bool:
    """Send one finished recording back through transcription; ``False`` when it cannot go.

    One id at a time and never automatic: this re-downloads audio, spends minutes of GPU
    and asks for a fresh ✅, so it is a decision rather than a sweep. The old approval
    card is **not** deleted — unlike a repost it carries a decision the owner actually
    made, and the record it belonged to is already written.
    """
    record = load_state(store.state_path).records.get(recording_id)
    if record is None:
        return False
    reset = reset_for_reprocess(record)
    if reset is None:
        return False
    if record.message_id is not None:
        # store.update refuses to move a message binding; clearing it is that path.
        store.clear_message_id(record.recording_id, record.action_hash, record.message_id)
    store.update(reset)
    return True
