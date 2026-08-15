from __future__ import annotations

from automation.hermes_compat.owner_dm_relatedness import (
    DmSignal,
    Relatedness,
    classify,
    merge_window_seconds,
)

_LAST_TS: dict[str, float] = {}


def relatedness_for(
    session_key: str,
    *,
    owner_id: str,
    timestamp: float,
    reply_to_message_id: str | None,
    has_media: bool,
    is_internal: bool,
    tail_message_id: str | None,
    last_physical_timestamp: float | None = None,
) -> Relatedness:
    prev = _LAST_TS.get(session_key)
    signal = DmSignal(
        owner_id=owner_id,
        dm_session_id=session_key,
        is_command=False,
        has_media=has_media,
        is_internal=is_internal,
        is_reply=reply_to_message_id is not None,
        reply_target_message_id=reply_to_message_id,
        timestamp=timestamp,
        prev_physical_timestamp=prev,
        tail_physical_message_id=tail_message_id,
    )
    _LAST_TS[session_key] = timestamp if last_physical_timestamp is None else last_physical_timestamp
    return classify(signal, window_seconds=merge_window_seconds())


def reset(session_key: str | None = None) -> None:
    if session_key is None:
        _LAST_TS.clear()
        return
    _ = _LAST_TS.pop(session_key, None)
