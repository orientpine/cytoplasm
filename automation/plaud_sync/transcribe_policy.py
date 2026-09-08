"""Persisted retry timing policy; the clock is injected, never read by tests."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from .model import PlaudSyncRecord
from .duration import DEFAULT_MIN_DURATION_MS

DEFAULT_GIVE_UP: Final = 5


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 2
    give_up: int = DEFAULT_GIVE_UP
    now: Callable[[], datetime] = utc_now
    min_duration_ms: int = DEFAULT_MIN_DURATION_MS

    def next_at(self, attempts: int) -> str:
        hours = min(1 << min(max(attempts - self.max_attempts, 0), 5), 24)
        return (self.now().astimezone(UTC) + timedelta(hours=hours)).isoformat()

    def waiting(self, record: PlaudSyncRecord) -> bool:
        return record.next_transcribe_at is not None and self.now() < datetime.fromisoformat(record.next_transcribe_at)
