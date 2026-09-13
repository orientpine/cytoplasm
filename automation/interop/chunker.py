"""Ordered Discord-compatible message chunking."""

from __future__ import annotations

import re
from typing import Final


DISCORD_MESSAGE_LIMIT: Final = 2_000
_URL_TOKEN: Final = re.compile(r"https?://[^\s]*")


def chunk_message(message: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Return contiguous, order-preserving chunks no longer than ``limit``."""
    if limit < 1:
        raise ValueError("limit must be positive")
    chunks: list[str] = []
    start = 0
    # One global scan keeps embedded schemes inside their original URL span.
    tokens = _URL_TOKEN.finditer(message)
    token = next(tokens, None)
    while start < len(message):
        end = min(start + limit, len(message))
        while token is not None and token.end() <= end:
            token = next(tokens, None)
        if (
            token is not None
            and start < token.start() < end < token.end()
            and token.end() - token.start() <= limit
        ):
            end = token.start()
        chunks.append(message[start:end])
        start = end
    return chunks or [""]
