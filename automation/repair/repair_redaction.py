"""Shared W1-7-compatible masking for repair-ticket surfaces."""

from __future__ import annotations

import hashlib
import re
from typing import Final


REDACTIONS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{6,}"), "[MASKED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[MASKED_TOKEN]"),
    (re.compile(r"\b(?:Bearer|Bot)\s+\S+", re.IGNORECASE), "[MASKED_AUTH]"),
    (re.compile(r"\b[A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}\b"), "[MASKED_TOKEN]"),
    (re.compile(r"\b\d{17,19}\b"), "[MASKED_ID]"),
)


def redact(text: str) -> str:
    """Mask token and identifier shapes before any non-private persistence."""
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def digest(text: str) -> str:
    """Return a stable SHA-256 digest without retaining the source string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def excerpt(raw_log: str, limit: int = 400) -> str:
    """Produce the bounded Kanban-safe projection of a private log."""
    return redact(raw_log).replace("\x00", "")[:limit]
