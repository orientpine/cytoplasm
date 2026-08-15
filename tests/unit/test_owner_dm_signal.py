from __future__ import annotations

from collections.abc import Iterator

import pytest

from automation.hermes_compat import owner_dm_signal
from automation.hermes_compat.owner_dm_relatedness import Relatedness
from automation.hermes_compat.owner_dm_signal import relatedness_for, reset


@pytest.fixture(autouse=True)
def reset_last_timestamps() -> Iterator[None]:
    reset()
    yield
    reset()


def test_relatedness_for_returns_separate_for_first_signal_and_records_timestamp() -> None:
    result = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=10.0,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    assert result is Relatedness.SEPARATE
    assert getattr(owner_dm_signal, "_LAST_TS")["session-a"] == 10.0


def test_relatedness_for_merges_a_second_signal_within_default_window() -> None:
    _ = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=10.0,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    result = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=10.5,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    assert result is Relatedness.MERGE_TAIL


def test_relatedness_for_separates_a_second_signal_outside_default_window() -> None:
    _ = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=10.0,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    result = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=15.0,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    assert result is Relatedness.SEPARATE


def test_relatedness_for_merges_reply_to_tail_regardless_of_gap() -> None:
    _ = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=10.0,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    result = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=20.0,
        reply_to_message_id="tail",
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    assert result is Relatedness.MERGE_TAIL


@pytest.mark.parametrize(("has_media", "is_internal"), ((True, False), (False, True)))
def test_relatedness_for_separates_media_and_internal_signals(
    has_media: bool,
    is_internal: bool,
) -> None:
    _ = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=10.0,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    result = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=10.5,
        reply_to_message_id=None,
        has_media=has_media,
        is_internal=is_internal,
        tail_message_id="tail",
    )

    assert result is Relatedness.SEPARATE


def test_relatedness_for_uses_configured_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_OWNER_DM_MERGE_WINDOW_SECONDS", "2.5")
    _ = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=10.0,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    result = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=12.0,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    assert result is Relatedness.MERGE_TAIL


def test_reset_for_session_removes_its_last_timestamp() -> None:
    _ = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=10.0,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )
    reset("session-a")

    result = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=10.5,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    assert result is Relatedness.SEPARATE


def test_reset_without_session_removes_all_last_timestamps() -> None:
    _ = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=10.0,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )
    _ = relatedness_for(
        "session-b",
        owner_id="owner",
        timestamp=20.0,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )
    reset()

    assert getattr(owner_dm_signal, "_LAST_TS") == {}


def test_relatedness_for_keeps_timestamps_independent_between_sessions() -> None:
    _ = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=10.0,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    result = relatedness_for(
        "session-b",
        owner_id="owner",
        timestamp=10.5,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    assert result is Relatedness.SEPARATE


def test_relatedness_for_measures_gap_from_last_physical_timestamp() -> None:
    # A multi-message batch: first ts 10.0 but its last physical DM arrived at 10.9.
    _ = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=10.0,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
        last_physical_timestamp=10.9,
    )

    # The next DM at 11.5 is 0.6s from the last physical DM (<=1.0) -> merge,
    # even though it is 1.5s from the batch's first-message timestamp.
    result = relatedness_for(
        "session-a",
        owner_id="owner",
        timestamp=11.5,
        reply_to_message_id=None,
        has_media=False,
        is_internal=False,
        tail_message_id="tail",
    )

    assert result is Relatedness.MERGE_TAIL
    assert getattr(owner_dm_signal, "_LAST_TS")["session-a"] == 11.5
