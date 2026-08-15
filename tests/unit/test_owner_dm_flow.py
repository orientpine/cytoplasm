from __future__ import annotations

import asyncio

import pytest
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final, override

from automation.hermes_compat.owner_dm_dispatch import (
    RouteOutcome,
    prepend,
    queue_depth,
    route,
)
from automation.hermes_compat.owner_dm_relatedness import DmSignal, classify
from automation.hermes_compat.receipt_apply import ReactionAdapter, resolve_receipts
from automation.hermes_compat.receipt_tracker import (
    RECEIPT_MEMBERS_KEY,
    RECEIPT_MESSAGE_IDS_KEY,
)


_CAP: Final = 32
_MERGE_WINDOW_SECONDS: Final = 1.0
_OWNER_ID: Final = "owner-1"
_SESSION_KEY: Final = "dm-session-1"
_RECEIVED: Final = "👀"
_SUCCEEDED: Final = "✅"
_FAILED: Final = "❌"


@final
class FakeReactionRecorder:
    def __init__(self) -> None:
        self.applied: dict[str, list[str]] = {}

    def add(self, message_id: str, reaction: str) -> None:
        self.applied.setdefault(message_id, []).append(reaction)


@dataclass(frozen=True, slots=True)
class FakeRawMessage:
    message_id: str
    recorder: FakeReactionRecorder

    def add_reaction(self, reaction: str) -> None:
        self.recorder.add(self.message_id, reaction)


Metadata = dict[str, list[str | FakeRawMessage]]


@dataclass(slots=True)
class FakeEvent:
    """Model the mutable gateway event that pure routing merges into its tail."""

    message_id: str
    text: str
    timestamp: float
    raw_message: FakeRawMessage
    metadata: Metadata
    media_urls: list[str]
    is_command: bool
    is_reply: bool
    reply_target_message_id: str | None


@final
class OwnerDmFlowHarness:
    def __init__(self) -> None:
        self.reactions: FakeReactionRecorder = FakeReactionRecorder()
        self.pending_slot: dict[str, object] = {}
        self.overflow: dict[str, list[object]] = {}
        self.previous_physical_timestamp: float | None = None
        self.processed_texts: list[str] = []
        self.turn_trace: list[tuple[str, str]] = []

    def event(self, message_id: str, text: str, timestamp: float) -> FakeEvent:
        metadata: Metadata = {
            RECEIPT_MESSAGE_IDS_KEY: [],
            RECEIPT_MEMBERS_KEY: [],
        }
        return FakeEvent(
            message_id=message_id,
            text=text,
            timestamp=timestamp,
            raw_message=FakeRawMessage(message_id, self.reactions),
            metadata=metadata,
            media_urls=[],
            is_command=False,
            is_reply=False,
            reply_target_message_id=None,
        )

    def receive(self, event: FakeEvent, *, busy: bool) -> RouteOutcome:
        self._record_receipt(event)
        tail = self._tail()
        tail_message_id = None
        if tail is not None:
            tail_message_id = tail.metadata[RECEIPT_MESSAGE_IDS_KEY][-1]
            assert isinstance(tail_message_id, str)
        outcome = route(
            self.pending_slot,
            self.overflow,
            _SESSION_KEY,
            event,
            classify(
                DmSignal(
                    owner_id=_OWNER_ID,
                    dm_session_id=_SESSION_KEY,
                    is_command=event.is_command,
                    has_media=bool(event.media_urls),
                    is_internal=False,
                    is_reply=event.is_reply,
                    reply_target_message_id=event.reply_target_message_id,
                    timestamp=event.timestamp,
                    prev_physical_timestamp=self.previous_physical_timestamp,
                    tail_physical_message_id=tail_message_id,
                ),
                window_seconds=_MERGE_WINDOW_SECONDS,
            ),
            cap=_CAP,
        )
        self.previous_physical_timestamp = event.timestamp
        if outcome is RouteOutcome.REJECTED_OVER_CAP:
            event.raw_message.add_reaction(_FAILED)
        if not busy:
            self.drain()
        return outcome

    def prepend_current(self, event: FakeEvent) -> None:
        self._record_receipt(event)
        self.previous_physical_timestamp = event.timestamp
        prepend(self.pending_slot, self.overflow, _SESSION_KEY, event)

    def drain(self, *, succeeded: bool = True) -> None:
        while (event := self._pop_head()) is not None:
            self.turn_trace.append(("start", event.message_id))
            self.processed_texts.append(event.text)
            reaction = _SUCCEEDED if succeeded else _FAILED
            for raw_message in event.metadata[RECEIPT_MEMBERS_KEY]:
                assert isinstance(raw_message, FakeRawMessage)
                raw_message.add_reaction(reaction)
            self.turn_trace.append(("complete", event.message_id))
            self._promote_next()

    def _record_receipt(self, event: FakeEvent) -> None:
        event.raw_message.add_reaction(_RECEIVED)
        event.metadata[RECEIPT_MESSAGE_IDS_KEY] = [event.message_id]
        event.metadata[RECEIPT_MEMBERS_KEY] = [event.raw_message]

    def _tail(self) -> FakeEvent | None:
        queued = self.overflow.get(_SESSION_KEY)
        candidate = queued[-1] if queued else self.pending_slot.get(_SESSION_KEY)
        if candidate is None:
            return None
        assert isinstance(candidate, FakeEvent)
        return candidate

    def _pop_head(self) -> FakeEvent | None:
        candidate = self.pending_slot.pop(_SESSION_KEY, None)
        if candidate is None:
            return None
        assert isinstance(candidate, FakeEvent)
        return candidate

    def _promote_next(self) -> None:
        queued = self.overflow.get(_SESSION_KEY)
        if queued:
            self.pending_slot[_SESSION_KEY] = queued.pop(0)
            if not queued:
                del self.overflow[_SESSION_KEY]


def test_s1_unrelated_messages_become_fifo_turns() -> None:
    # Given
    driver = OwnerDmFlowHarness()
    first = driver.event("m1", "A", 0.0)
    second = driver.event("m2", "B", 5.0)

    # When
    _ = driver.receive(first, busy=True)
    _ = driver.receive(second, busy=True)
    driver.drain()

    # Then
    assert driver.processed_texts == ["A", "B"]
    assert driver.reactions.applied == {"m1": ["👀", "✅"], "m2": ["👀", "✅"]}


def test_s2a_burst_messages_merge_into_one_turn() -> None:
    # Given
    driver = OwnerDmFlowHarness()
    first = driver.event("m1", "A", 0.0)
    second = driver.event("m2", "B", 0.5)

    # When
    _ = driver.receive(first, busy=True)
    _ = driver.receive(second, busy=True)
    driver.drain()

    # Then
    assert driver.processed_texts == ["A\nB"]
    assert driver.reactions.applied == {"m1": ["👀", "✅"], "m2": ["👀", "✅"]}


def test_s2b_reply_to_tail_merges_after_window() -> None:
    # Given
    driver = OwnerDmFlowHarness()
    first = driver.event("m1", "A", 0.0)
    reply = driver.event("m2", "B", 10.0)
    reply.is_reply = True
    reply.reply_target_message_id = "m1"

    # When
    _ = driver.receive(first, busy=True)
    _ = driver.receive(reply, busy=True)
    driver.drain()

    # Then
    assert driver.processed_texts == ["A\nB"]
    assert driver.reactions.applied == {"m1": ["👀", "✅"], "m2": ["👀", "✅"]}


def test_s3_every_physical_message_receives_receipt_and_final_state() -> None:
    # Given
    driver = OwnerDmFlowHarness()
    events = [
        driver.event("m1", "A", 0.0),
        driver.event("m2", "B", 0.5),
        driver.event("m3", "C", 5.0),
        driver.event("m4", "D", 5.5),
        driver.event("m5", "E", 10.0),
    ]

    # When
    for event in events:
        _ = driver.receive(event, busy=True)
    driver.drain()

    # Then
    reactions = [reaction for applied in driver.reactions.applied.values() for reaction in applied]
    assert reactions.count("👀") == 5
    assert sum(reaction in {"✅", "❌"} for reaction in reactions) == 5


def test_s4_over_cap_rejects_the_physical_message() -> None:
    # Given
    driver = OwnerDmFlowHarness()
    for index in range(32):
        outcome = driver.receive(
            driver.event(f"m{index}", str(index), float(index * 2)), busy=True
        )
        assert outcome is RouteOutcome.APPENDED
    rejected = driver.event("m33", "overflow", 66.0)

    # When
    outcome = driver.receive(rejected, busy=True)

    # Then
    assert outcome is RouteOutcome.REJECTED_OVER_CAP
    assert queue_depth(driver.pending_slot, driver.overflow, _SESSION_KEY) == 32
    assert driver.reactions.applied["m33"] == ["👀", "❌"]


def test_s5_prepended_current_item_runs_before_queued_item() -> None:
    # Given
    driver = OwnerDmFlowHarness()
    next_item = driver.event("next", "next", 5.0)
    current_item = driver.event("current", "current", 10.0)
    _ = driver.receive(next_item, busy=True)

    # When
    driver.prepend_current(current_item)
    driver.drain()

    # Then
    assert driver.processed_texts == ["current", "next"]
    assert driver.reactions.applied == {
        "next": ["👀", "✅"],
        "current": ["👀", "✅"],
    }


def test_s7_idle_message_completes_normally() -> None:
    # Given
    driver = OwnerDmFlowHarness()
    event = driver.event("idle", "idle", 0.0)

    # When
    _ = driver.receive(event, busy=False)

    # Then
    assert driver.processed_texts == ["idle"]
    assert driver.reactions.applied == {"idle": ["👀", "✅"]}


def test_s8_busy_fifo_keeps_the_current_turn_whole() -> None:
    # Given
    driver = OwnerDmFlowHarness()
    first = driver.event("long", "long", 0.0)
    next_item = driver.event("next", "next", 5.0)
    source = Path(__file__).read_text(encoding="utf-8")

    # When
    _ = driver.receive(first, busy=True)
    _ = driver.receive(next_item, busy=True)
    driver.drain()

    # Then
    assert all(term not in source for term in ("inter" + "rupt", "st" + "eer"))
    assert driver.turn_trace == [
        ("start", "long"),
        ("complete", "long"),
        ("start", "next"),
        ("complete", "next"),
    ]


def test_s9_command_and_media_events_never_merge_with_text() -> None:
    # Given
    driver = OwnerDmFlowHarness()
    text = driver.event("text", "text", 0.0)
    command = driver.event("command", "/help", 0.5)
    media = driver.event("media", "photo", 1.0)
    command.is_command = True
    media.media_urls.append("https://media")

    # When
    _ = driver.receive(text, busy=True)
    _ = driver.receive(command, busy=True)
    _ = driver.receive(media, busy=True)
    driver.drain()

    # Then
    assert driver.processed_texts == ["text", "/help", "photo"]
    assert driver.reactions.applied == {
        "text": ["👀", "✅"],
        "command": ["👀", "✅"],
        "media": ["👀", "✅"],
    }



@final
class FakeResolveAdapter(ReactionAdapter):
    """Adapter stand-in for resolve_receipts: records each member's final reaction."""

    def __init__(self) -> None:
        self.final: dict[str, str] = {}
        self.order: list[str] = []

    @override
    def _reactions_enabled(self) -> bool:
        return True

    @override
    async def _remove_reaction(self, message: object, emoji: str) -> None:
        _ = (message, emoji)

    @override
    async def _add_reaction(self, message: object, emoji: str) -> None:
        message_id = getattr(message, "message_id", "?")
        assert isinstance(message_id, str)
        self.final[message_id] = emoji
        self.order.append(message_id)


def _receipt_event(message_id: str, recorder: FakeReactionRecorder) -> FakeEvent:
    member = FakeRawMessage(message_id, recorder)
    metadata: Metadata = {
        RECEIPT_MESSAGE_IDS_KEY: [message_id],
        RECEIPT_MEMBERS_KEY: [member],
    }
    return FakeEvent(
        message_id=message_id,
        text=message_id,
        timestamp=0.0,
        raw_message=member,
        metadata=metadata,
        media_urls=[],
        is_command=False,
        is_reply=False,
        reply_target_message_id=None,
    )


def test_s3_recursive_followups_resolve_with_their_own_turn_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Locks the injected run.py contract for a depth-first 3-turn drain (original -> B -> C):
    # each parent frame stashes its follow-up event under (session_key, depth+1) BEFORE recursing;
    # each frame, at its OWN result-determination, pops (session_key, depth) and resolves it with
    # ITS OWN turn outcome. Outcomes differ per turn, so the old one-level result shift (B taking
    # C's outcome) would be visible here.
    monkeypatch.setenv("HOME", str(tmp_path))
    recorder = FakeReactionRecorder()
    adapter = FakeResolveAdapter()
    original = _receipt_event("m1", recorder)
    follow_b = _receipt_event("m2", recorder)
    follow_c = _receipt_event("m3", recorder)
    children: dict[int, FakeEvent] = {1: follow_b, 2: follow_c}
    outcomes: dict[int, bool] = {0: True, 1: False, 2: True}  # B fails; original + C succeed
    stash: dict[tuple[str, int], FakeEvent] = {}

    async def frame(depth: int) -> None:
        popped = stash.pop((_SESSION_KEY, depth), None)
        if popped is not None:
            await resolve_receipts(adapter, popped, ok=outcomes[depth])
        child = children.get(depth + 1)
        if child is not None:
            stash[(_SESSION_KEY, depth + 1)] = child  # parent stashes child before recursing
            await frame(depth + 1)

    async def run() -> None:
        await frame(0)  # depth 0's original is not stashed; on_processing_complete resolves it:
        await resolve_receipts(adapter, original, ok=outcomes[0])

    asyncio.run(run())

    # Each physical DM keeps ITS OWN turn's outcome — B stays failed, not C's success.
    assert adapter.final == {"m2": "\N{CROSS MARK}", "m3": "\N{WHITE HEAVY CHECK MARK}", "m1": "\N{WHITE HEAVY CHECK MARK}"}
    # Follow-ups finalize in turn-completion order (B before C); the original resolves last.
    assert adapter.order == ["m2", "m3", "m1"]


def test_s6_followup_frame_crash_before_result_finalizes_receipt_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # If a follow-up frame exits (exception/cancel) before its result-determination
    # pop, the parent frame's finally cleanup finalizes that DM as ❌ so it is never
    # left stuck at 👀 / received (BLOCKER 1 regression).
    monkeypatch.setenv("HOME", str(tmp_path))
    recorder = FakeReactionRecorder()
    adapter = FakeResolveAdapter()
    follow_b = _receipt_event("m2", recorder)
    children: dict[int, FakeEvent] = {1: follow_b}
    stash: dict[tuple[str, int], FakeEvent] = {}

    async def frame(depth: int) -> None:
        if depth == 1:
            raise RuntimeError("follow-up frame died before result-determination")
        _ = stash.pop((_SESSION_KEY, depth), None)
        child = children.get(depth + 1)
        if child is not None:
            stash[(_SESSION_KEY, depth + 1)] = child  # parent stashes before recursing
            try:
                await frame(depth + 1)
            finally:  # child never popped its own event -> parent finalizes it failed
                orphan = stash.pop((_SESSION_KEY, depth + 1), None)
                if orphan is not None:
                    await resolve_receipts(adapter, orphan, ok=False)

    async def run() -> None:
        with pytest.raises(RuntimeError):
            await frame(0)

    asyncio.run(run())

    assert adapter.final == {"m2": "\N{CROSS MARK}"}
    assert stash == {}