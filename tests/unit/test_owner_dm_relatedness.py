from __future__ import annotations

from dataclasses import replace

import pytest

from automation.hermes_compat.owner_dm_relatedness import (
    DmSignal,
    Relatedness,
    classify,
    merge_window_seconds,
)


_BASE_SIGNAL = DmSignal(
    owner_id="owner",
    dm_session_id="dm-session",
    is_command=False,
    has_media=False,
    is_internal=False,
    is_reply=False,
    reply_target_message_id=None,
    timestamp=10.0,
    prev_physical_timestamp=9.0,
    tail_physical_message_id="tail-message",
)


def test_classify_returns_separate_when_gap_exceeds_window() -> None:
    signal = replace(_BASE_SIGNAL, timestamp=15.0, prev_physical_timestamp=10.0)

    assert classify(signal, window_seconds=1.0) is Relatedness.SEPARATE


def test_classify_returns_merge_tail_when_text_burst_is_within_window() -> None:
    signal = replace(_BASE_SIGNAL, timestamp=10.5, prev_physical_timestamp=10.0)

    assert classify(signal, window_seconds=1.0) is Relatedness.MERGE_TAIL


def test_classify_returns_merge_tail_when_reply_targets_tail_after_gap() -> None:
    signal = replace(
        _BASE_SIGNAL,
        is_reply=True,
        reply_target_message_id="tail-message",
        timestamp=20.0,
        prev_physical_timestamp=10.0,
    )

    assert classify(signal, window_seconds=1.0) is Relatedness.MERGE_TAIL


def test_classify_returns_separate_when_signal_is_command() -> None:
    signal = replace(_BASE_SIGNAL, is_command=True, timestamp=10.5, prev_physical_timestamp=10.0)

    assert classify(signal, window_seconds=1.0) is Relatedness.SEPARATE


def test_classify_returns_separate_when_signal_has_media() -> None:
    signal = replace(_BASE_SIGNAL, has_media=True, timestamp=10.5, prev_physical_timestamp=10.0)

    assert classify(signal, window_seconds=1.0) is Relatedness.SEPARATE


def test_classify_returns_separate_when_signal_is_internal() -> None:
    signal = replace(_BASE_SIGNAL, is_internal=True, timestamp=10.5, prev_physical_timestamp=10.0)

    assert classify(signal, window_seconds=1.0) is Relatedness.SEPARATE


def test_classify_returns_merge_tail_when_gap_equals_window() -> None:
    signal = replace(_BASE_SIGNAL, timestamp=11.0, prev_physical_timestamp=10.0)

    assert classify(signal, window_seconds=1.0) is Relatedness.MERGE_TAIL


def test_classify_returns_separate_when_reply_targets_another_message_after_gap() -> None:
    signal = replace(
        _BASE_SIGNAL,
        is_reply=True,
        reply_target_message_id="other-message",
        timestamp=15.0,
        prev_physical_timestamp=10.0,
    )

    assert classify(signal, window_seconds=1.0) is Relatedness.SEPARATE


def test_classify_returns_separate_when_no_previous_physical_message_exists() -> None:
    signal = replace(_BASE_SIGNAL, prev_physical_timestamp=None)

    assert classify(signal, window_seconds=1.0) is Relatedness.SEPARATE


def test_merge_window_seconds_returns_environment_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_OWNER_DM_MERGE_WINDOW_SECONDS", "2.5")

    assert merge_window_seconds() == 2.5


def test_merge_window_seconds_returns_default_when_environment_is_unparseable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HERMES_OWNER_DM_MERGE_WINDOW_SECONDS", "abc")

    assert merge_window_seconds() == 1.0


def test_merge_window_seconds_returns_default_when_environment_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HERMES_OWNER_DM_MERGE_WINDOW_SECONDS", raising=False)

    assert merge_window_seconds() == 1.0
