"""Shared minimum recording length and closing transition (no audio work)."""
from __future__ import annotations

from dataclasses import replace
from typing import Final

from .model import PlaudSyncRecord
from .transcribe_model import Outcome, TranscribeEffects

DEFAULT_MIN_DURATION_MS: Final = 5000


def abandon_short(record: PlaudSyncRecord, effects: TranscribeEffects, duration_ms: int) -> Outcome:
    after = replace(
        record, status="abandoned", next_transcribe_at=None,
        last_block_reason=f"{duration_ms / 1000:g}초 녹음 — 최소 길이 미만",
    )
    return "abandoned" if effects.commit(record, after, None) else "stale"
