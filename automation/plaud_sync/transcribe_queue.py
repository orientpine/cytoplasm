"""Transcription candidate ordering, independent of local work and I/O."""
from __future__ import annotations

from typing import Final

from .model import PlaudSyncRecord, PlaudSyncState

#: A recording this long or longer waits behind shorter ones. One local run of a day-long piece holds
#: the single slot for the whole cron job (2026-11-23..26 the 24-hour pieces of one recording kept
#: everything recorded after them waiting), while a meeting-length recording finishes in minutes.
DEFAULT_LONG_RECORDING_MS: Final = 3 * 60 * 60 * 1000


def _is_long(record: PlaudSyncRecord, long_ms: int = DEFAULT_LONG_RECORDING_MS) -> bool:
    """A record from before lengths were stored counts as long: it cannot prove it is short."""
    return record.duration_ms is None or record.duration_ms >= long_ms


def candidates(
    state: PlaudSyncState, *, limit: int, long_ms: int = DEFAULT_LONG_RECORDING_MS
) -> tuple[PlaudSyncRecord, ...]:
    """Short before long, then least-tried, then oldest; run_step applies backoff before assigning slots."""
    waiting = sorted(
        (record for record in state.records.values() if record.status == "transcribing"),
        key=lambda record: (
            _is_long(record, long_ms), record.transcribe_attempts, record.recorded_at, record.recording_id,
        ),
    )
    return tuple(waiting[: max(limit, 0)])
