"""Render-independent digest binding shared by stored approval readers."""
from __future__ import annotations

import hashlib


def hash_parts(*parts: str) -> str:
    """The supply-chain hash preimage, unchanged from skill_gate_specs._hash."""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def content_matches(content: str, expected_digest: str | None) -> bool:
    """Validate stored bytes, without replaying or normalizing a rendered card."""
    return hash_parts(content) == expected_digest
