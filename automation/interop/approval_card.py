"""Approval wording selection, independent of lifecycle identity and clocks."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

MAX_CONTENT: Final = 2000


class CardRenderError(ValueError):
    """The approval cannot be presented; no external effect may start."""


@dataclass(frozen=True, slots=True)
class PreparedCard:
    render_version: str
    content: str


def prepare(renderer: Callable[[str], str], stored_version: str | None = None) -> PreparedCard:
    """New cards try v2; stored versions replay strictly without silent downgrades."""
    if stored_version is not None:
        try:
            content = renderer(stored_version)
        except ImportError as error:
            raise CardRenderError("stored card renderer unavailable") from error
        version = stored_version
    else:
        try:
            content = renderer("2")
            version = "2"
        except (ImportError, CardRenderError):
            content = renderer("1")
            version = "1"
    if not content or len(content) > MAX_CONTENT:
        raise CardRenderError("approval card exceeds the postable length")
    return PreparedCard(version, content)
