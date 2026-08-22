"""Read the two inputs: Obsidian source notes and the wiki bodies already stored."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeAlias

from automation.rag_ingest.chunking import parse_frontmatter
from automation.wiki_curate.candidates import SourceNote, content_digest

Classifier: TypeAlias = Callable[[str], frozenset[str]]

_SENSITIVE = "patent-sensitive"


def _markdown_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [
        path
        for path in sorted(root.rglob("*.md"))
        if not any(part.startswith(".") for part in path.relative_to(root).parts)
    ]


def _list_field(frontmatter: dict[str, str], key: str) -> tuple[str, ...]:
    raw = frontmatter.get(key, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _event_date(frontmatter: dict[str, str]) -> str | None:
    for key in ("event_date", "date", "day"):
        value = frontmatter.get(key, "").strip()
        if len(value) == 10 and value[4] == "-" and value[7] == "-":
            return value
    return None


def read_obsidian_notes(root: Path, *, classifier: Classifier) -> tuple[SourceNote, ...]:
    notes: list[SourceNote] = []
    for path in _markdown_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, body = parse_frontmatter(text)
        relative = path.relative_to(root).as_posix()
        tags = classifier(f"{frontmatter.get('title', '')} {body}")
        notes.append(
            SourceNote(
                ref=relative,
                title=frontmatter.get("title", "").strip() or path.stem,
                body=body,
                tags=_list_field(frontmatter, "tags"),
                sensitivity=_SENSITIVE if _SENSITIVE in tags else None,
                event_date=_event_date(frontmatter),
                entities=_list_field(frontmatter, "entity"),
            )
        )
    return tuple(notes)


ORIGIN_PREDICATE = "source:"


def read_wiki_origins(root: Path) -> frozenset[str]:
    """Which Obsidian refs the wiki already carries, read from each note's relations.

    This is the dedup key that survives distillation — the stored body is a
    summary, so it never matches the source body's digest.
    """
    origins: set[str] = set()
    for path in _markdown_files(root):
        frontmatter, _ = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        for relation in _list_field(frontmatter, "relations"):
            if relation.startswith(ORIGIN_PREDICATE):
                target = relation[len(ORIGIN_PREDICATE):].strip()
                if target:
                    origins.add(target)
    return frozenset(origins)


def read_wiki_digests(root: Path) -> frozenset[str]:
    """Content digests of the notes already in the wiki — the storage-side dedup key."""
    digests: set[str] = set()
    for path in _markdown_files(root):
        _, body = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if body.strip():
            digests.add(content_digest(body))
    return frozenset(digests)
