"""Logical document model shared by all sources (pure logic)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .hashing import content_sha256, doc_fingerprint, document_id


@dataclass(frozen=True)
class Chunk:
    source: str
    content: str
    metadata: dict[str, str]

    @property
    def point_id(self) -> str:
        return document_id(self.source, self.content)


@dataclass(frozen=True)
class LogicalDocument:
    """One ingestable unit (a wiki note, a meeting doc, a peer report...).

    ``source_key`` is the stable identity used for state tracking;
    chunk sources are ``{source_key}#c{index:04d}``.
    """

    source_key: str
    chunks: tuple[Chunk, ...]
    cursor_updates: dict[str, str] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        return doc_fingerprint([(chunk.source, chunk.content) for chunk in self.chunks])

    @property
    def point_ids(self) -> list[str]:
        return [chunk.point_id for chunk in self.chunks]


def build_document(
    source_key: str,
    chunk_texts: list[str],
    base_metadata: dict[str, str],
    cursor_updates: dict[str, str] | None = None,
) -> LogicalDocument:
    """Assemble a LogicalDocument with per-chunk provenance metadata."""
    total = len(chunk_texts)
    chunks: list[Chunk] = []
    for index, text in enumerate(chunk_texts):
        chunk_metadata = dict(base_metadata)
        chunk_metadata["chunk_index"] = str(index)
        chunk_metadata["chunk_total"] = str(total)
        chunk_metadata["content_sha256"] = content_sha256(text)
        chunks.append(
            Chunk(
                source=f"{source_key}#c{index:04d}",
                content=text,
                metadata=chunk_metadata,
            )
        )
    return LogicalDocument(
        source_key=source_key,
        chunks=tuple(chunks),
        cursor_updates=dict(cursor_updates or {}),
    )
