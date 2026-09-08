"""Owner-facing tick journal from persisted transcription outcomes (no I/O)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .model import PlaudSyncState
from .transcribe_model import Outcome


@dataclass(frozen=True, slots=True)
class StepSummary:
    line: str | None
    promoted: int


def summarize(outcomes: tuple[tuple[str, Outcome], ...], state: PlaudSyncState) -> StepSummary:
    counts = Counter(outcome for _, outcome in outcomes)
    lines = [
        f"plaud-sync: transcribed={counts['planned']} fallback={counts['fallback']} "
        + f"retry={counts['retry']} stale={counts['stale']} "
        + f"waiting={counts['waiting']} abandoned={counts['abandoned']}"
    ]
    for recording_id, outcome in outcomes:
        record = state.records[recording_id]
        if outcome in {"waiting", "abandoned"} or (outcome == "retry" and record.next_transcribe_at):
            lines.append(
                f"plaud-sync: recording_id={recording_id} outcome={outcome} "
                + f"next_transcribe_at={record.next_transcribe_at or '-'} "
                + f"reason={record.last_block_reason or '-'}"
            )
    return StepSummary("\n".join(lines), counts["planned"] + counts["fallback"])
