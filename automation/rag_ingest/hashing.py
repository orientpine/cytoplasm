"""Content hashing and document-id derivation.

The MCP memory server (configs/rag/mcp/src/rag_mcp/store.py) computes

    document_id = uuid5(NAMESPACE_URL, f"{source}\n{content}")

and upserts the Qdrant point under that id. Re-loading identical
(source, content) therefore overwrites the same point: 0 duplicates.
This module mirrors that derivation so the client can track point ids for
stale-chunk deletion without extra round trips.
"""

from __future__ import annotations

import hashlib
from uuid import NAMESPACE_URL, uuid5


def content_sha256(content: str) -> str:
    """Stable content hash used for client-side change detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def document_id(source: str, content: str) -> str:
    """Mirror of the server-side uuid5 point id (idempotent upsert key)."""
    return str(uuid5(NAMESPACE_URL, f"{source}\n{content}"))


def doc_fingerprint(chunk_sources_and_contents: list[tuple[str, str]]) -> str:
    """Hash of a whole logical document (all its chunk (source, content) pairs).

    Used as the queue/state change-detection key: identical fingerprint means
    re-ingesting would be a no-op, so the pipeline skips it entirely.
    """
    digest = hashlib.sha256()
    for source, content in chunk_sources_and_contents:
        digest.update(source.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(content.encode("utf-8"))
        digest.update(b"\x01")
    return digest.hexdigest()
