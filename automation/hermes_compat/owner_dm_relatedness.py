from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Final


_DEFAULT_WINDOW_SECONDS: Final = 1.0


class Relatedness(Enum):
    SEPARATE = "separate"
    MERGE_TAIL = "merge_tail"


@dataclass(frozen=True, slots=True)
class DmSignal:
    owner_id: str
    dm_session_id: str
    is_command: bool
    has_media: bool
    is_internal: bool
    is_reply: bool
    reply_target_message_id: str | None
    timestamp: float
    prev_physical_timestamp: float | None
    tail_physical_message_id: str | None


def classify(signal: DmSignal, *, window_seconds: float) -> Relatedness:
    if signal.is_command or signal.has_media or signal.is_internal:
        return Relatedness.SEPARATE
    if (
        signal.is_reply
        and signal.reply_target_message_id is not None
        and signal.reply_target_message_id == signal.tail_physical_message_id
    ):
        return Relatedness.MERGE_TAIL
    if (
        signal.prev_physical_timestamp is not None
        and signal.timestamp - signal.prev_physical_timestamp <= window_seconds
    ):
        return Relatedness.MERGE_TAIL
    return Relatedness.SEPARATE


def merge_window_seconds() -> float:
    configured_window = os.environ.get("HERMES_OWNER_DM_MERGE_WINDOW_SECONDS")
    if not configured_window:
        return _DEFAULT_WINDOW_SECONDS
    try:
        return float(configured_window)
    except ValueError:
        return _DEFAULT_WINDOW_SECONDS
