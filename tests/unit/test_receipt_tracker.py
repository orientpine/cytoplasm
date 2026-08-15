from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    RECEIPT_MESSAGE_IDS_KEY = "receipt_message_ids"
    RECEIPT_MEMBERS_KEY = "receipt_members"

    class ReceiptTracker:
        def register_physical(self, turn_key: str, message_id: str, member: object) -> None:
            _ = turn_key
            _ = message_id
            _ = member

        def attach_metadata(self, turn_key: str) -> dict[str, object]:
            _ = turn_key
            return {}

        def pop_turn_receipts(self, turn_key: str) -> tuple[list[str], list[object]]:
            _ = turn_key
            return list[str](), list[object]()

        def set_lifecycle(self, turn_key: str, ctx: object) -> None:
            _ = turn_key
            _ = ctx

        def get_lifecycle(self, turn_key: str) -> object | None:
            _ = turn_key
            return None

        def clear(self, turn_key: str) -> None:
            _ = turn_key

    def tracker() -> ReceiptTracker:
        return ReceiptTracker()

else:
    from automation.hermes_compat.receipt_tracker import (
        RECEIPT_MEMBERS_KEY,
        RECEIPT_MESSAGE_IDS_KEY,
        ReceiptTracker,
        tracker,
    )


def test_attach_metadata_preserves_physical_receipt_order() -> None:
    # Given
    receipt_tracker = ReceiptTracker()
    first_member = object()
    second_member = object()
    receipt_tracker.register_physical("turn-1", "message-1", first_member)
    receipt_tracker.register_physical("turn-1", "message-2", second_member)

    # When
    metadata = receipt_tracker.attach_metadata("turn-1")

    # Then
    assert metadata == {
        RECEIPT_MESSAGE_IDS_KEY: ["message-1", "message-2"],
        RECEIPT_MEMBERS_KEY: [first_member, second_member],
    }


def test_attach_metadata_returns_copies() -> None:
    # Given
    receipt_tracker = ReceiptTracker()
    member = object()
    receipt_tracker.register_physical("turn-1", "message-1", member)
    metadata = receipt_tracker.attach_metadata("turn-1")

    # When
    metadata[RECEIPT_MESSAGE_IDS_KEY] = ["changed"]
    metadata[RECEIPT_MEMBERS_KEY] = []

    # Then
    assert receipt_tracker.attach_metadata("turn-1") == {
        RECEIPT_MESSAGE_IDS_KEY: ["message-1"],
        RECEIPT_MEMBERS_KEY: [member],
    }


def test_pop_turn_receipts_returns_then_clears_receipts() -> None:
    # Given
    receipt_tracker = ReceiptTracker()
    first_member = object()
    second_member = object()
    receipt_tracker.register_physical("turn-1", "message-1", first_member)
    receipt_tracker.register_physical("turn-1", "message-2", second_member)

    # When
    popped = receipt_tracker.pop_turn_receipts("turn-1")

    # Then
    assert popped == (["message-1", "message-2"], [first_member, second_member])
    assert receipt_tracker.pop_turn_receipts("turn-1") == ([], [])


def test_register_physical_deduplicates_by_message_id() -> None:
    # Given
    receipt_tracker = ReceiptTracker()
    first_member = object()
    duplicate_member = object()
    receipt_tracker.register_physical("turn-1", "message-1", first_member)

    # When
    receipt_tracker.register_physical("turn-1", "message-1", duplicate_member)

    # Then
    assert receipt_tracker.pop_turn_receipts("turn-1") == (["message-1"], [first_member])


def test_receipt_metadata_keys_match_gateway_contract() -> None:
    # Given / When / Then
    assert RECEIPT_MESSAGE_IDS_KEY == "receipt_message_ids"
    assert RECEIPT_MEMBERS_KEY == "receipt_members"


def test_lifecycle_context_roundtrips_and_clear_removes_it() -> None:
    # Given
    receipt_tracker = ReceiptTracker()
    lifecycle_context = object()
    receipt_tracker.set_lifecycle("turn-1", lifecycle_context)

    # When
    stored_context = receipt_tracker.get_lifecycle("turn-1")
    receipt_tracker.clear("turn-1")

    # Then
    assert stored_context is lifecycle_context
    assert receipt_tracker.get_lifecycle("turn-1") is None


def test_tracker_returns_process_global_singleton() -> None:
    # Given / When
    first_tracker = tracker()
    second_tracker = tracker()

    # Then
    assert first_tracker is second_tracker
