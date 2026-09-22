"""Ordered Discord-compatible message chunking.

한 벌뿐인 분할기다. 고정폭으로 자르는 `chunk_message` 와 줄 경계로 채우는 `chunk_lines`
가 같은 URL 규칙을 공유한다 — 사본이 생기면 한쪽만 링크를 지키고 다른 쪽은 자른다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Final


DISCORD_MESSAGE_LIMIT: Final = 2_000
#: 머리글 길이가 조각 수에 되먹임되므로 고정점까지 다시 센다. 자릿수는 유한해 곧 멎는다.
_HEADER_PASSES: Final = 8
_URL_TOKEN: Final = re.compile(r"https?://[^\s]*")


def chunk_message(
    message: str, limit: int = DISCORD_MESSAGE_LIMIT, *, preserve_urls: bool = True
) -> list[str]:
    """Return contiguous, order-preserving chunks no longer than ``limit``."""
    if limit < 1:
        raise ValueError("limit must be positive")
    chunks: list[str] = []
    start = 0
    # One global scan keeps embedded schemes inside their original URL span.
    tokens = _URL_TOKEN.finditer(message) if preserve_urls else iter(())
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


def _packed(body: str, budget: int, preserve_urls: bool) -> list[str]:
    """줄 단위로 채운다 — 예산보다 긴 줄만 쪼개고(URL 은 피해서), 버리지는 않는다."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in body.splitlines():
        for piece in chunk_message(line, budget, preserve_urls=preserve_urls):
            if current and size + len(piece) + 1 > budget:
                chunks.append("\n".join(current))
                current, size = [piece], len(piece)
                continue
            size += len(piece) + (1 if current else 0)
            current.append(piece)
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


def chunk_lines(
    body: str, *, limit: int, header: Callable[[int, int], str],
    preserve_urls: bool = True,
) -> tuple[str, ...]:
    """줄 경계로 나눈 조각들 — 각 조각이 제 머리글을 달고 ``limit`` 을 넘지 않는다.

    ``header(index, total)`` 가 조각마다 앞에 붙으므로 예산은 그만큼 줄고, 예산이 줄면 조각
    수가 늘어 머리글 길이가 또 바뀐다. 그래서 조각 수가 제자리에 설 때까지 다시 센다.
    """
    total = 1
    for _ in range(_HEADER_PASSES):
        pieces = _packed(body, limit - len(header(total, total)) - 1, preserve_urls)
        if len(pieces) == total:
            return tuple(
                f"{header(index, total)}\n{piece}"
                for index, piece in enumerate(pieces, start=1)
            )
        total = len(pieces)
    raise ValueError("headed chunks do not converge on a stable count")
