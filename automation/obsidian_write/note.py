"""Deterministic PARA placement and Obsidian callout rendering."""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from .config import ObsidianWriteError

_PERSONAL_PARA_ROOT: Final = PurePosixPath("000_PARA")
_KIMM_PARA_ROOT: Final = PurePosixPath("001_KIMM_PARA")
_UNCLASSIFIED_INBOX: Final = _PERSONAL_PARA_ROOT / "Area" / "000_정리되지않은생각들"
_BUCKET_NAMES: Final = (
    ("project", "Project"),
    ("area", "Area"),
    ("resource", "Resource"),
    ("archive", "Archive"),
)
_FILENAME_LIMIT: Final = 80


@dataclass(frozen=True, slots=True)
class NotePlan:
    """A normalized, deterministic destination and the note's source content."""

    relpath: PurePosixPath
    title: str
    body: str


def plan_note(
    title: str,
    body: str,
    *,
    institutional: bool,
    bucket_hint: str | None,
) -> NotePlan:
    """Normalize a note into one stable PARA path without writing it."""
    normalized_title = _normalize_title(title)
    bucket = _recognized_bucket(bucket_hint)
    parent = _note_parent(institutional, bucket)
    filename = _filename_for_title(normalized_title)
    return NotePlan(parent / filename, normalized_title, body.strip())


def render_note(plan: NotePlan, *, created: str, modified: str) -> str:
    """Render the vault's heading-and-callout format, or hoist a body-owned frontmatter.

    A body that opens with a closed '---' block (plaud lifelog v2, 2026-09-04) is a
    note whose metadata the owner's Linter manages as YAML: the block goes above the
    H1 and the info callout is omitted — tags/created/modified already live in the
    YAML and the location is the path. Bodies without such a block render byte for
    byte as before (wiki·memory notes are untouched).
    """
    yaml_block, body = _split_frontmatter(plan.body)
    if yaml_block is not None:
        return f"{yaml_block}\n\n# {plan.title}\n\n{body}\n"
    tag = "#KIMM" if plan.relpath.parts[0] == _KIMM_PARA_ROOT.name else "#personal"
    return (
        f"# {plan.title}\n\n"
        ">[!info]\n"
        "> Author: cha\n"
        f"> Created: {created}\n"
        f"> Modified: {modified}\n"
        f"> Location: {plan.relpath.parent.as_posix()}\n"
        f"> Tag: {tag}\n\n"
        f"{plan.body}\n"
    )


def _split_frontmatter(body: str) -> tuple[str | None, str]:
    """(closed leading '---' block, rest) — an unclosed leading rule is ordinary body."""
    lines = body.split("\n")
    if lines[0] != "---":
        return None, body
    for index in range(1, len(lines)):
        if lines[index] == "---":
            return "\n".join(lines[: index + 1]), "\n".join(lines[index + 1 :]).strip()
    return None, body


def _normalize_title(title: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", title).split())
    if not normalized:
        raise ObsidianWriteError("Obsidian note title is empty", False)
    return normalized


def _recognized_bucket(bucket_hint: str | None) -> str | None:
    if bucket_hint is None:
        return None
    normalized_hint = unicodedata.normalize("NFKC", bucket_hint).strip().casefold()
    for hint, bucket in _BUCKET_NAMES:
        if normalized_hint == hint:
            return bucket
    return None


def _note_parent(institutional: bool, bucket: str | None) -> PurePosixPath:
    if bucket is None:
        return _UNCLASSIFIED_INBOX
    root = _KIMM_PARA_ROOT if institutional else _PERSONAL_PARA_ROOT
    return root / bucket


def _filename_for_title(title: str) -> str:
    filename_stem = "".join(
        character if character.isalnum() or character in {" ", "-", "_"} else " "
        for character in title
    )
    normalized_stem = "-".join(filename_stem.split())[:_FILENAME_LIMIT].strip("-_")
    safe_stem = normalized_stem or "note"
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
    return f"{safe_stem}--{digest}.md"
