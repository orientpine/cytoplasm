from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final, Protocol, cast

from automation.hermes_compat.receipt_ledger import ReceiptLedger, default_ledger_path
from automation.hermes_compat.receipt_tracker import (
    RECEIPT_MEMBERS_KEY,
    RECEIPT_MESSAGE_IDS_KEY,
)

RESOLVED_FLAG: Final = "_autophagy_receipt_resolved"
_WATCHING: Final = "\N{EYES}"
_OK_EMOJI: Final = "\N{WHITE HEAVY CHECK MARK}"
_FAIL_EMOJI: Final = "\N{CROSS MARK}"

_ReactionCall = Callable[[object, str], Awaitable[object]]


class ReactionAdapter(Protocol):
    """Structural view of the Discord adapter methods the boundary needs."""

    def _reactions_enabled(self) -> bool: ...

    async def _remove_reaction(self, message: object, emoji: str) -> object: ...

    async def _add_reaction(self, message: object, emoji: str) -> object: ...


def _metadata(event: object) -> dict[object, object]:
    metadata = getattr(event, "metadata", None)
    return cast("dict[object, object]", metadata) if isinstance(metadata, dict) else {}


def receipt_members(event: object) -> list[object]:
    """Return every physical DM message object attached to a logical turn."""
    members = _metadata(event).get(RECEIPT_MEMBERS_KEY)
    if isinstance(members, list) and members:
        return list(cast("list[object]", members))
    raw = getattr(event, "raw_message", None)
    return [raw] if raw is not None else []


def receipt_message_ids(event: object) -> list[str]:
    """Return the physical DM message ids recorded for a logical turn."""
    ids = _metadata(event).get(RECEIPT_MESSAGE_IDS_KEY)
    if isinstance(ids, list):
        return [str(value) for value in cast("list[object]", ids) if str(value)]
    return []


def already_resolved(event: object) -> bool:
    return bool(getattr(event, RESOLVED_FLAG, False))


def _mark_resolved(event: object) -> None:
    try:
        setattr(event, RESOLVED_FLAG, True)
    except Exception:
        pass


def _reactions_on(adapter: ReactionAdapter) -> bool:
    checker = getattr(adapter, "_reactions_enabled", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return False


async def _swap_reaction(adapter: ReactionAdapter, member: object, emoji: str) -> None:
    remove = cast("_ReactionCall | None", getattr(adapter, "_remove_reaction", None))
    add = cast("_ReactionCall | None", getattr(adapter, "_add_reaction", None))
    try:
        if remove is not None:
            _ = await remove(member, _WATCHING)
        if add is not None:
            _ = await add(member, emoji)
    except Exception:
        pass


def _resolve_ledger(message_ids: list[str], *, ok: bool) -> None:
    if not message_ids:
        return
    try:
        ledger = ReceiptLedger(default_ledger_path())
    except Exception:
        return
    for message_id in message_ids:
        try:
            ledger.resolve(message_id, ok)
        except Exception:
            pass


async def resolve_receipts(adapter: ReactionAdapter, event: object, *, ok: bool) -> None:
    """Idempotently finalize one owner-DM turn.

    Swaps the in-progress 👀 for ✅ (ok) or ❌ (not ok) on every physical DM of the
    turn and flips the content-free ledger row for each. Safe to call more than once
    for the same event (e.g. from both the FIFO continuation loop and the outer
    ``on_processing_complete``): the first call wins, later calls are no-ops.
    Any non-success outcome (failure, cancellation) maps to ``ok=False`` at the call
    site, so cancelled turns are finalized as ❌ instead of being left at 👀.
    """
    if already_resolved(event):
        return
    _mark_resolved(event)
    # Flip the crash-visibility ledger FIRST: a cancellation while awaiting the
    # reaction API then still leaves a durable resolved_ok/resolved_fail record.
    _resolve_ledger(receipt_message_ids(event), ok=ok)
    emoji = _OK_EMOJI if ok else _FAIL_EMOJI
    if _reactions_on(adapter):
        for member in receipt_members(event):
            if member is not None and hasattr(member, "add_reaction"):
                await _swap_reaction(adapter, member, emoji)
