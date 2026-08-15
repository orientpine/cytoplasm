from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, final, override

from automation.hermes_compat import receipt_apply
from automation.hermes_compat.receipt_apply import ReactionAdapter, resolve_receipts
from automation.hermes_compat.receipt_ledger import ReceiptLedger
from automation.hermes_compat.receipt_tracker import (
    RECEIPT_MEMBERS_KEY,
    RECEIPT_MESSAGE_IDS_KEY,
)

if TYPE_CHECKING:
    import pytest


@final
class FakeMember:
    """A physical DM message stand-in that accepts reactions."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def add_reaction(self, emoji: str) -> None:  # marks it reactable
        _ = emoji


@final
class PlainMember:
    """A message-like object WITHOUT add_reaction (not reactable)."""


@final
class FakeEvent:
    def __init__(
        self,
        *,
        metadata: dict[str, object] | None = None,
        raw_message: object | None = None,
    ) -> None:
        self.metadata = metadata
        self.raw_message = raw_message


@final
class FakeAdapter(ReactionAdapter):
    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self.removed: list[tuple[str, str]] = []
        self.added: list[tuple[str, str]] = []

    @override
    def _reactions_enabled(self) -> bool:
        return self._enabled

    @override
    async def _remove_reaction(self, message: object, emoji: str) -> None:
        self.removed.append((getattr(message, "name", "?"), emoji))

    @override
    async def _add_reaction(self, message: object, emoji: str) -> None:
        self.added.append((getattr(message, "name", "?"), emoji))


def _seed_ledger(home: Path, rows: list[tuple[str, str]]) -> ReceiptLedger:
    ledger = ReceiptLedger(home / ".hermes" / "owner-dm-receipts" / "receipts.sqlite3")
    for channel_id, message_id in rows:
        ledger.record_received(channel_id, message_id)
    return ledger


def test_resolve_success_swaps_watching_for_check_and_flips_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("HOME", str(tmp_path))
    ledger = _seed_ledger(tmp_path, [("c", "m1"), ("c", "m2")])
    first, second = FakeMember("m1"), FakeMember("m2")
    event = FakeEvent(
        metadata={
            RECEIPT_MEMBERS_KEY: [first, second],
            RECEIPT_MESSAGE_IDS_KEY: ["m1", "m2"],
        }
    )
    adapter = FakeAdapter()

    # When
    asyncio.run(resolve_receipts(adapter, event, ok=True))

    # Then
    assert adapter.removed == [("m1", "\N{EYES}"), ("m2", "\N{EYES}")]
    assert adapter.added == [
        ("m1", "\N{WHITE HEAVY CHECK MARK}"),
        ("m2", "\N{WHITE HEAVY CHECK MARK}"),
    ]
    assert ledger.states() == {"m1": "resolved_ok", "m2": "resolved_ok"}


def test_resolve_failure_uses_cross_mark_and_marks_ledger_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("HOME", str(tmp_path))
    ledger = _seed_ledger(tmp_path, [("c", "m1")])
    member = FakeMember("m1")
    event = FakeEvent(
        metadata={RECEIPT_MEMBERS_KEY: [member], RECEIPT_MESSAGE_IDS_KEY: ["m1"]}
    )
    adapter = FakeAdapter()

    # When (a cancelled/failed turn maps to ok=False at the call site)
    asyncio.run(resolve_receipts(adapter, event, ok=False))

    # Then
    assert adapter.added == [("m1", "\N{CROSS MARK}")]
    assert ledger.states() == {"m1": "resolved_fail"}


def test_resolve_is_idempotent_across_two_call_sites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("HOME", str(tmp_path))
    _ = _seed_ledger(tmp_path, [("c", "m1")])
    member = FakeMember("m1")
    event = FakeEvent(
        metadata={RECEIPT_MEMBERS_KEY: [member], RECEIPT_MESSAGE_IDS_KEY: ["m1"]}
    )
    adapter = FakeAdapter()

    # When (continuation loop resolves, then outer on_processing_complete re-fires)
    asyncio.run(resolve_receipts(adapter, event, ok=True))
    asyncio.run(resolve_receipts(adapter, event, ok=False))

    # Then only the first resolution applied
    assert adapter.added == [("m1", "\N{WHITE HEAVY CHECK MARK}")]
    assert receipt_apply.already_resolved(event) is True


def test_reactions_disabled_still_resolves_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("HOME", str(tmp_path))
    ledger = _seed_ledger(tmp_path, [("c", "m1")])
    event = FakeEvent(
        metadata={RECEIPT_MEMBERS_KEY: [FakeMember("m1")], RECEIPT_MESSAGE_IDS_KEY: ["m1"]}
    )
    adapter = FakeAdapter(enabled=False)

    # When
    asyncio.run(resolve_receipts(adapter, event, ok=True))

    # Then
    assert adapter.added == []
    assert ledger.states() == {"m1": "resolved_ok"}


def test_members_fall_back_to_raw_message_without_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("HOME", str(tmp_path))
    raw = FakeMember("raw")
    event = FakeEvent(metadata=None, raw_message=raw)
    adapter = FakeAdapter()

    # When
    asyncio.run(resolve_receipts(adapter, event, ok=True))

    # Then
    assert adapter.added == [("raw", "\N{WHITE HEAVY CHECK MARK}")]


def test_non_reactable_member_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    monkeypatch.setenv("HOME", str(tmp_path))
    event = FakeEvent(
        metadata={RECEIPT_MEMBERS_KEY: [PlainMember()], RECEIPT_MESSAGE_IDS_KEY: []}
    )
    adapter = FakeAdapter()

    # When
    asyncio.run(resolve_receipts(adapter, event, ok=True))

    # Then
    assert adapter.added == []
