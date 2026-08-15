"""Ordered Discord-compatible message chunking."""

from typing import Final


DISCORD_MESSAGE_LIMIT: Final = 2_000


def chunk_message(message: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Return contiguous, order-preserving chunks no longer than ``limit``."""
    if limit < 1:
        raise ValueError("limit must be positive")
    return [message[index : index + limit] for index in range(0, len(message), limit)] or [""]
