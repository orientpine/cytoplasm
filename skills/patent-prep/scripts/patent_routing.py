"""Call planning using the account model configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


CODEX_PROVIDER: Final = "openai-codex"
CODEX_MODEL: Final = "hermes-config"


@dataclass(frozen=True, slots=True)
class PatentCall:
    """The shared account route and requested tags."""

    provider: str
    model: str
    tags: tuple[str, ...]


def plan_patent_call(requested_tags: tuple[str, ...] = ()) -> PatentCall:
    """Normalize caller-provided tags without adding content labels."""
    tags = tuple(dict.fromkeys(tag.strip() for tag in requested_tags if tag.strip()))
    return PatentCall(CODEX_PROVIDER, CODEX_MODEL, tags)
