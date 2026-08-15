"""Fail-closed non-GLM call planning for patent-sensitive work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


PATENT_SENSITIVE_TAG: Final = "patent-sensitive"
CODEX_PROVIDER: Final = "openai-codex"
CODEX_MODEL: Final = "gpt-5.4"


@dataclass(frozen=True, slots=True)
class PatentCall:
    """The only LLM route available to this skill."""

    provider: str
    model: str
    tags: tuple[str, ...]
    tag_auto_attached: bool


def plan_patent_call(requested_tags: tuple[str, ...] = ()) -> PatentCall:
    """Normalize tags, attach the required guard tag, and hard-code Codex."""
    normalized = tuple(dict.fromkeys(tag.strip() for tag in requested_tags if tag.strip()))
    auto_attached = PATENT_SENSITIVE_TAG not in normalized
    tags = normalized + (PATENT_SENSITIVE_TAG,) if auto_attached else normalized
    return PatentCall(CODEX_PROVIDER, CODEX_MODEL, tags, auto_attached)
