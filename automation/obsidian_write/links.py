"""Wikilink check and related-note suggestions against the read-only vault mirror.

A note the owner approves should link to notes that exist. Obsidian silently creates
an empty "ghost" page for every ``[[target]]`` it cannot resolve, so a link the agent
invented looks like a real link until someone clicks it. This module reads the
pull-only RAG mirror (never the write clone) and answers two questions before the
approval card is posted: which links in the body resolve to nothing, and which
existing notes look related enough to suggest. Both are advisory and fail-soft —
an unreadable mirror never blocks a request, it is reported on the card instead.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

_FENCE: Final = re.compile(r"^(```|~~~)")
_INLINE_CODE: Final = re.compile(r"`[^`\n]*`")
_WIKILINK: Final = re.compile(r"!?\[\[([^\[\]\n]+?)\]\]")
_SKIP_DIRS: Final = frozenset({".git", ".obsidian", ".trash"})
_WORD: Final = re.compile(r"[0-9A-Za-z가-힣]{2,}")
RELATED_LIMIT: Final = 5


def _key(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip().casefold()


@dataclass(frozen=True, slots=True)
class VaultIndex:
    """Lookup keys for every file in the mirror: note stems, relpaths and filenames."""

    relpaths: frozenset[str]
    names: frozenset[str]
    stems: dict[str, str]

    def resolves(self, target: str) -> bool:
        key = _key(target)
        if not key:
            return True
        bare = key.removesuffix(".md")
        if bare in self.relpaths or key in self.relpaths or key in self.names:
            return True
        if "/" not in bare:
            return bare in self.stems
        return any(path == bare or path.endswith(f"/{bare}") for path in self.relpaths)


def build_index(mirror: Path) -> VaultIndex | None:
    """Walk the mirror once; ``None`` when it cannot be read (reported, not fatal)."""
    if not mirror.is_dir():
        return None
    relpaths: set[str] = set()
    names: set[str] = set()
    stems: dict[str, str] = {}
    try:
        for root, dirs, files in os.walk(mirror):
            dirs[:] = [name for name in dirs if name not in _SKIP_DIRS]
            for name in files:
                relative = PurePosixPath(Path(root, name).relative_to(mirror).as_posix())
                names.add(_key(name))
                if name.endswith(".md"):
                    relpaths.add(_key(relative.with_suffix("").as_posix()))
                    stems.setdefault(_key(relative.stem), relative.stem)
                else:
                    relpaths.add(_key(relative.as_posix()))
    except OSError:
        return None
    return VaultIndex(frozenset(relpaths), frozenset(names), stems)


def wikilink_targets(body: str) -> tuple[str, ...]:
    """Link targets in reading order, without alias, heading or block reference.

    ``[[note|alias]]``, ``[[note#heading]]``, ``[[note#^block]]``, embeds ``![[x]]``
    and the table-escaped ``[[note\\|alias]]`` all reduce to ``note``. A link that is
    only a heading (``[[#section]]``) points inside this note and yields nothing.
    Code fences and inline code are not links.
    """
    targets: list[str] = []
    fenced = False
    for line in body.splitlines():
        if _FENCE.match(line.lstrip()):
            fenced = not fenced
            continue
        if fenced:
            continue
        for match in _WIKILINK.finditer(_INLINE_CODE.sub("", line)):
            inner = match.group(1).replace("\\|", "|")
            target = inner.split("|", 1)[0].split("#", 1)[0].strip()
            if target and target not in targets:
                targets.append(target)
    return tuple(targets)


def unresolved_links(body: str, index: VaultIndex, *, own_stem: str) -> tuple[str, ...]:
    own = _key(own_stem)
    return tuple(
        target for target in wikilink_targets(body)
        if _key(target) != own and not index.resolves(target)
    )


RelatedSearch = Callable[[str], Iterable[str]]


def knowledge_search(text: str) -> tuple[str, ...]:
    """Obsidian note paths the shared search index returns for ``text`` (read-only)."""
    from automation.knowledge.facade import collect_evidence
    from automation.knowledge.pack import KnowledgeQuery

    pack = collect_evidence(
        KnowledgeQuery(text=text, sources=frozenset({"rag"}), limit=12, caller="obsidian_write")
    )
    return tuple(item.ref for item in pack.items if item.store == "obsidian" and item.ref)


def related_notes(
    title: str,
    body: str,
    index: VaultIndex,
    *,
    own_stem: str,
    search: RelatedSearch = knowledge_search,
) -> tuple[tuple[str, ...], str]:
    """Up to five existing note titles to suggest, and where they came from.

    The search index ranks by content; when it is unreachable the fallback ranks
    mirror filenames by words shared with the title, so a suggestion still exists.
    Every suggestion must resolve in the mirror, and notes already linked are skipped.
    """
    linked = {_key(target) for target in wikilink_targets(body)} | {_key(own_stem)}
    source = "search-index"
    try:
        stems = [PurePosixPath(ref).stem for ref in search(f"{title}\n{body[:400]}")]
    except Exception:  # noqa: BLE001 - suggestions are advisory; any failure falls back
        stems, source = [], "unavailable"
    if not stems:
        stems, source = _filename_overlap(title, index), (
            "filename" if source == "search-index" else "filename(search unavailable)"
        )
    chosen: list[str] = []
    for stem in stems:
        if _key(stem) not in linked and index.resolves(stem) and stem not in chosen:
            chosen.append(index.stems.get(_key(stem), stem))
        if len(chosen) == RELATED_LIMIT:
            break
    return tuple(chosen), source


def _filename_overlap(title: str, index: VaultIndex) -> list[str]:
    words = {_key(word) for word in _WORD.findall(title)}
    scored = (
        (sum(1 for word in words if word in key), stem)
        for key, stem in index.stems.items()
    )
    return [stem for score, stem in sorted(scored, key=lambda pair: (-pair[0], pair[1])) if score >= 2]
