"""File-based sources: wiki vault (W2-2), personal notes, meeting summaries.

Scans a directory of markdown files, parses frontmatter, chunks the body and
emits LogicalDocuments. Also reports the set of present source keys so the
pipeline can delete vectors for files that were removed.
"""

from __future__ import annotations

from pathlib import Path

from ..chunking import chunk_markdown, parse_frontmatter
from ..documents import LogicalDocument, build_document
from ..metadata import build_metadata

_FRONTMATTER_KEYS = (
    "title",
    "tags",
    "created",
    "updated",
    "links",
    "kind",
    "authority",
    "provenance",
    "status",
    "review_after",
    "supersedes",
)


def _iter_markdown_files(root: Path, exclude_dirs: tuple[Path, ...] = ()) -> list[Path]:
    if not root.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if any(exclude == path.parent or exclude in path.parents for exclude in exclude_dirs):
            continue
        files.append(path)
    return files


def scan_directory(
    root: Path,
    prefix: str,
    source_type: str,
    perspective: dict[str, str],
    max_chunk_chars: int,
    exclude_dirs: tuple[Path, ...] = (),
) -> tuple[list[LogicalDocument], set[str]]:
    """Return (documents, present source keys) for one markdown directory."""
    documents: list[LogicalDocument] = []
    present_keys: set[str] = set()
    for path in _iter_markdown_files(root, exclude_dirs):
        relative = path.relative_to(root).as_posix()
        source_key = f"{prefix}:{relative}"
        present_keys.add(source_key)
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = parse_frontmatter(text)
        chunk_texts = chunk_markdown(body, max_chunk_chars)
        if not chunk_texts:
            continue
        extra: dict[str, str] = {"path": relative}
        for key in _FRONTMATTER_KEYS:
            value = frontmatter.get(key, "")
            if value:
                extra[key] = value
        extra.setdefault("title", path.stem)
        base_metadata = build_metadata(perspective, source_type, extra)
        documents.append(build_document(source_key, chunk_texts, base_metadata))
    return documents, present_keys
