from __future__ import annotations

from typing import Final


RECEIPT_MESSAGE_IDS_KEY: Final = "receipt_message_ids"
RECEIPT_MEMBERS_KEY: Final = "receipt_members"
RECEIPT_LAST_TS_KEY: Final = "receipt_last_physical_ts"


class ReceiptTracker:
    """Maintain mutable process-local receipt and lifecycle state per logical turn."""

    def __init__(self) -> None:
        self._receipts: dict[str, tuple[list[str], list[object]]] = {}
        self._lifecycle: dict[str, object] = {}

    def register_physical(self, turn_key: str, message_id: str, member: object) -> None:
        message_ids, members = self._receipts.setdefault(turn_key, ([], []))
        if message_id not in message_ids:
            message_ids.append(message_id)
            members.append(member)

    def attach_metadata(self, turn_key: str) -> dict[str, object]:
        message_ids, members = self._receipts.get(turn_key, ([], []))
        return {
            RECEIPT_MESSAGE_IDS_KEY: list(message_ids),
            RECEIPT_MEMBERS_KEY: list(members),
        }

    def pop_turn_receipts(self, turn_key: str) -> tuple[list[str], list[object]]:
        message_ids, members = self._receipts.pop(turn_key, ([], []))
        return list(message_ids), list(members)

    def set_lifecycle(self, turn_key: str, ctx: object) -> None:
        self._lifecycle[turn_key] = ctx

    def get_lifecycle(self, turn_key: str) -> object | None:
        return self._lifecycle.get(turn_key)

    def clear(self, turn_key: str) -> None:
        _ = self._receipts.pop(turn_key, None)
        _ = self._lifecycle.pop(turn_key, None)


_TRACKER: Final = ReceiptTracker()


def tracker() -> ReceiptTracker:
    return _TRACKER
