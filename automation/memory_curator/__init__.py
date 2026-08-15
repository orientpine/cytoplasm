"""Hermes native-memory curator (pure logic).

Keeps the capped MEMORY.md / USER.md files tidy and under cap:
autonomous lossless compaction, plus owner-gated flagging of durable
judgment for promotion into the decision twin.  See ``model`` and
``curator`` for the pure API; file application / cron live elsewhere.
"""

from __future__ import annotations

from .curator import NEAR_CAP_RATIO, curate, parse_memory_file, serialize_memory_file
from .model import CAPS, CurationPlan, MemoryEntry, MemoryFile, MemoryKind

__all__ = [
    "CAPS",
    "NEAR_CAP_RATIO",
    "CurationPlan",
    "MemoryEntry",
    "MemoryFile",
    "MemoryKind",
    "curate",
    "parse_memory_file",
    "serialize_memory_file",
]
