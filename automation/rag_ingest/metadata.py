"""My-perspective metadata construction (pure logic).

Team knowledge is deliberately duplicated per person (user decision
2026-07-13); every vector this agent stores carries the agent's own
perspective so W2-5 recall can attribute and rank from *my* point of view.
The MCP payload metadata type is ``dict[str, str]``.
"""

from __future__ import annotations

_MAX_VALUE_CHARS = 500

PERSPECTIVE_KEYS = ("agent_id", "owner", "role", "project", "interest_tags")


def build_metadata(
    perspective: dict[str, str],
    source_type: str,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Merge perspective + source-specific fields into flat string metadata.

    ``source_type`` is one of: wiki | note | meeting | conversation |
    team-chat | peer-report. Extra keys never override perspective keys.
    """
    merged: dict[str, str] = {}
    for key in PERSPECTIVE_KEYS:
        value = perspective.get(key, "")
        if value:
            merged[key] = str(value)[:_MAX_VALUE_CHARS]
    merged["source_type"] = source_type
    for key, value in (extra or {}).items():
        if key in merged:
            continue
        text = str(value)
        if text:
            merged[key] = text[:_MAX_VALUE_CHARS]
    return merged
