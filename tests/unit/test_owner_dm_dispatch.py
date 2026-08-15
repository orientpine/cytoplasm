from __future__ import annotations

from pathlib import Path
from typing import ClassVar, final

from automation.hermes_compat import owner_dm_dispatch
from automation.hermes_compat.owner_dm_dispatch import (
    RouteOutcome,
    prepend,
    queue_depth,
    route,
)
from automation.hermes_compat.owner_dm_relatedness import Relatedness
from automation.hermes_compat.receipt_tracker import (
    RECEIPT_MEMBERS_KEY,
    RECEIPT_MESSAGE_IDS_KEY,
)


@final
class FakeEvent:
    __slots__: ClassVar[tuple[str, ...]] = ("media_urls", "metadata", "text")

    text: str
    metadata: dict[str, object]
    media_urls: list[str]

    def __init__(
        self,
        text: str,
        metadata: dict[str, object] | None = None,
        media_urls: list[str] | None = None,
    ) -> None:
        self.text = text
        self.metadata = {} if metadata is None else metadata
        self.media_urls = [] if media_urls is None else media_urls


def test_separate_events_append_as_distinct_fifo_turns() -> None:
    # Given
    pending_slot: dict[str, object] = {}
    overflow: dict[str, list[object]] = {}
    first = FakeEvent("A")
    second = FakeEvent("B")

    # When
    first_outcome = route(
        pending_slot,
        overflow,
        "session-1",
        first,
        Relatedness.SEPARATE,
        cap=32,
    )
    second_outcome = route(
        pending_slot,
        overflow,
        "session-1",
        second,
        Relatedness.SEPARATE,
        cap=32,
    )

    # Then
    assert first_outcome is RouteOutcome.APPENDED
    assert second_outcome is RouteOutcome.APPENDED
    assert pending_slot["session-1"] is first
    assert overflow["session-1"] == [second]
    assert first.text == "A"
    assert second.text == "B"


def test_merge_tail_extends_latest_turn_without_changing_depth() -> None:
    # Given
    first_member = object()
    second_member = object()
    head = FakeEvent("head")
    tail = FakeEvent(
        "A",
        {
            RECEIPT_MESSAGE_IDS_KEY: ["m1"],
            RECEIPT_MEMBERS_KEY: [first_member],
        },
        ["media-1"],
    )
    event = FakeEvent(
        "B",
        {
            RECEIPT_MESSAGE_IDS_KEY: ["m2"],
            RECEIPT_MEMBERS_KEY: [second_member],
        },
        ["media-2"],
    )
    pending_slot: dict[str, object] = {"session-1": head}
    overflow: dict[str, list[object]] = {"session-1": [tail]}
    depth_before = queue_depth(pending_slot, overflow, "session-1")

    # When
    outcome = route(
        pending_slot,
        overflow,
        "session-1",
        event,
        Relatedness.MERGE_TAIL,
        cap=32,
    )

    # Then
    assert outcome is RouteOutcome.MERGED_TAIL
    assert tail.text == "A\nB"
    assert tail.metadata[RECEIPT_MESSAGE_IDS_KEY] == ["m1", "m2"]
    assert tail.metadata[RECEIPT_MEMBERS_KEY] == [first_member, second_member]
    assert tail.media_urls == ["media-1", "media-2"]
    assert queue_depth(pending_slot, overflow, "session-1") == depth_before


def test_merge_tail_appends_when_queue_is_empty() -> None:
    # Given
    pending_slot: dict[str, object] = {}
    overflow: dict[str, list[object]] = {}
    event = FakeEvent("A")

    # When
    outcome = route(
        pending_slot,
        overflow,
        "session-1",
        event,
        Relatedness.MERGE_TAIL,
        cap=32,
    )

    # Then
    assert outcome is RouteOutcome.APPENDED
    assert pending_slot["session-1"] is event
    assert overflow == {}


def test_separate_event_is_rejected_at_queue_cap() -> None:
    # Given
    pending_slot: dict[str, object] = {}
    overflow: dict[str, list[object]] = {}
    for index in range(32):
        outcome = route(
            pending_slot,
            overflow,
            "session-1",
            FakeEvent(str(index)),
            Relatedness.SEPARATE,
            cap=32,
        )
        assert outcome is RouteOutcome.APPENDED

    # When
    outcome = route(
        pending_slot,
        overflow,
        "session-1",
        FakeEvent("33"),
        Relatedness.SEPARATE,
        cap=32,
    )

    # Then
    assert outcome is RouteOutcome.REJECTED_OVER_CAP
    assert queue_depth(pending_slot, overflow, "session-1") == 32


def test_prepend_places_event_before_existing_fifo() -> None:
    # Given
    head = FakeEvent("A")
    queued = FakeEvent("B")
    event = FakeEvent("X")
    pending_slot: dict[str, object] = {"session-1": head}
    overflow: dict[str, list[object]] = {"session-1": [queued]}

    # When
    prepend(pending_slot, overflow, "session-1", event)

    # Then
    assert pending_slot["session-1"] is event
    assert overflow["session-1"] == [head, queued]


def test_module_excludes_non_fifo_busy_turn_controls() -> None:
    # Given
    module_path = Path(owner_dm_dispatch.__file__)

    # When
    source = module_path.read_text()

    # Then
    assert "interrupt" not in source
    assert "steer" not in source
