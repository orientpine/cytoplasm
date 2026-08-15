"""Per-thread bot reply loop protection."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from hashlib import sha256
from typing import Final
from unicodedata import normalize

from automation.interop.delegation import parse_envelope
from automation.interop.report import parse_report


MAX_REPLIES_PER_WINDOW: Final = 5
WINDOW_SECONDS: Final = 60.0
MAX_LOW_VALUE_REPLIES_PER_WINDOW: Final = 2
NEAR_DUPLICATE_RATIO: Final = 0.85


@dataclass(frozen=True, slots=True)
class GuardDecision:
    """The observable result of evaluating one outbound bot reply."""

    suppressed: bool
    reason: str | None


@dataclass(slots=True)
class _ThreadWindow:
    """Mutable timestamps and hashes are the explicit state of one rate window."""

    timestamps: deque[float] = field(default_factory=deque)
    body_hashes: set[str] = field(default_factory=set)
    normalized_bodies: deque[str] = field(default_factory=deque)


@dataclass(slots=True)
class LoopGuard:
    """Suppress sustained low-value bot chatter without mutating valid protocol messages."""

    _threads: defaultdict[str, _ThreadWindow] = field(default_factory=lambda: defaultdict(_ThreadWindow))

    def evaluate(self, thread_id: str, body: str, now: float) -> GuardDecision:
        """Allow or suppress the next bot-to-bot reply using an injected clock."""
        if parse_envelope(body) is not None or parse_report(body) is not None:
            return GuardDecision(suppressed=False, reason=None)
        window = self._threads[thread_id]
        cutoff = now - WINDOW_SECONDS
        while window.timestamps and window.timestamps[0] <= cutoff:
            window.timestamps.popleft()
            window.normalized_bodies.popleft()
        if not window.timestamps:
            window.body_hashes.clear()

        normalized_body = _normalize(body)
        body_hash = sha256(normalized_body.encode("utf-8")).hexdigest()
        if body_hash in window.body_hashes:
            return GuardDecision(suppressed=True, reason="duplicate_body")
        if _is_low_information(normalized_body) and _low_value_count(window) >= MAX_LOW_VALUE_REPLIES_PER_WINDOW:
            return GuardDecision(suppressed=True, reason="low_value_chatter")
        if any(_similar(normalized_body, prior) for prior in window.normalized_bodies):
            return GuardDecision(suppressed=True, reason="near_duplicate_body")
        if len(window.timestamps) >= MAX_REPLIES_PER_WINDOW:
            return GuardDecision(suppressed=True, reason="rate_limit")

        window.timestamps.append(now)
        window.body_hashes.add(body_hash)
        window.normalized_bodies.append(normalized_body)
        return GuardDecision(suppressed=False, reason=None)


def _normalize(body: str) -> str:
    return " ".join(normalize("NFKC", body).casefold().split())


def _is_low_information(body: str) -> bool:
    return sum(character.isalnum() for character in body) <= 4


def _low_value_count(window: _ThreadWindow) -> int:
    return sum(_is_low_information(body) for body in window.normalized_bodies)


def _similar(current: str, prior: str) -> bool:
    return not _is_low_information(current) and SequenceMatcher(a=current, b=prior).ratio() >= NEAR_DUPLICATE_RATIO
