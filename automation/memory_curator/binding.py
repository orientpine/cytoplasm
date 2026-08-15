"""Exact deletion-safety bindings for native-memory promotion."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final

from .model import MemoryKind

ENTRY_PREIMAGE_VERSION: Final = "mc-entry-v1"
MARKER_VERSION: Final = "mc-marker-v1"

_MARKER_PREFIX: Final = "<!-- mc-marker-"
_MARKER_PATTERN: Final = re.compile(
    r"<!-- (?P<version>mc-marker-[0-9A-Za-z._-]+) "
    + r"key=(?P<key>[^\s<>]+) "
    + r"kind=(?P<kind>memory|user) "
    + r"digest=(?P<digest>[0-9a-f]{64}) "
    + r"delete-after-persist=(?P<delete>true|false) -->"
)


def entry_digest(source_kind: MemoryKind, entry_text: str) -> str:
    """Hash the exact source-qualified entry bytes without normalization."""
    preimage = (
        ENTRY_PREIMAGE_VERSION.encode()
        + b"\x00"
        + source_kind.encode()
        + b"\x00"
        + entry_text.encode("utf-8")
    )
    return hashlib.sha256(preimage).hexdigest()


def promotion_key(source_kind: MemoryKind, digest: str) -> str:
    return f"{source_kind}:{digest}"


def promoted_slug(source_kind: MemoryKind, digest: str) -> str:
    return f"memory-promoted-{source_kind}-{digest[:32]}"


@dataclass(frozen=True, slots=True)
class DeletionMarker:
    """Deletion authority embedded in a persisted wiki note."""

    version: str
    promotion_key: str
    source_kind: MemoryKind
    entry_digest: str
    delete_after_persist: bool


def render_marker(m: DeletionMarker) -> str:
    """Render one deterministic marker without allowing comment escape."""
    field_values = (m.version, m.promotion_key, m.source_kind, m.entry_digest)
    if any("\n" in value or "\r" in value or "-->" in value for value in field_values):
        message = "marker field values must not contain newlines or HTML comment terminators"
        raise ValueError(message)
    delete_after_persist = "true" if m.delete_after_persist else "false"
    return (
        f"<!-- {m.version} key={m.promotion_key} kind={m.source_kind} "
        f"digest={m.entry_digest} delete-after-persist={delete_after_persist} -->"
    )


def parse_marker(note_text: str) -> DeletionMarker | None:
    """Return one coherent v1 marker, failing closed on absence or ambiguity."""
    if note_text.count(_MARKER_PREFIX) != 1:
        return None
    matched = _MARKER_PATTERN.search(note_text)
    if matched is None or matched.group("version") != MARKER_VERSION:
        return None

    raw_source_kind = matched.group("kind")
    match raw_source_kind:
        case "memory":
            source_kind: MemoryKind = "memory"
        case "user":
            source_kind = "user"
        case _:
            return None

    digest = matched.group("digest")
    key = matched.group("key")
    if key != promotion_key(source_kind, digest):
        return None
    return DeletionMarker(
        version=MARKER_VERSION,
        promotion_key=key,
        source_kind=source_kind,
        entry_digest=digest,
        delete_after_persist=matched.group("delete") == "true",
    )


@dataclass(frozen=True, slots=True)
class PromotionReceipt:
    """Identifiers proving the promoted note was persisted and confirmed."""

    draft_id: str
    confirm_message_id: str
    slug: str
    note_sha256: str
