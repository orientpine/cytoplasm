"""Transcription candidate ordering, independent of local work and I/O."""
from __future__ import annotations

from .model import PlaudSyncRecord, PlaudSyncState


def candidates(state: PlaudSyncState, *, limit: int) -> tuple[PlaudSyncRecord, ...]:
    """Least-tried first, then oldest; run_step applies backoff before assigning slots."""
    waiting = sorted(
        (record for record in state.records.values() if record.status == "transcribing"),
        key=lambda record: (record.transcribe_attempts, record.recorded_at, record.recording_id),
    )
    return tuple(waiting[: max(limit, 0)])
