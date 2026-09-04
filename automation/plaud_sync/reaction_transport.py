"""plaud's import seam onto the shared reaction transport.

The Discord transport and the ✅→gate-record transcription used to be COPIED here:
the only implementation lived in ``memory_relocate.effects_live``, and importing that
module drags the whole memory_curator chain into this watcher. ``interop`` now owns
both (``automation.interop.reaction_approval`` imports no watcher package), so the
copy is gone and this file keeps only the import path the cron wrapper, the approval
gate and their tests already use.
"""

from __future__ import annotations

from automation.interop.reaction_approval import (
    DiscordTransport,
    DiscordTransportError,
    JsonValue,
    record_push_approval,
)

__all__ = [
    "DiscordTransport",
    "DiscordTransportError",
    "JsonValue",
    "record_push_approval",
]
