"""The owner's card lines from YooDori's daily memory copy, offered to every recall pick (Q433).

YooDori's omo memory is the source of truth (owner Q241). Once a day she renders its ``system/``
card lines and ``notes/facts/`` into one Obsidian note; the node keeps a read-only mirror of the
vault. That note is about 200,000 characters, so the search alone rarely surfaced the few card
lines about the owner himself (affiliation, rank, mail, family, standing rules). This module cuts
the card section out of the mirrored note so the pick always sees it in full. A missing note, an
unreadable mirror or a note without the section gives no card: recall then works as before.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

# Same fixed note as yoodori_read_gate.MEMORY_COPY_RELPATH and its read-only mirror (note-get).
RELPATH: Final = "000_PARA/Resource/유돌 기억 자동 사본.md"
MIRROR: Final = Path(".hermes/obsidian-mirror")
SECTION: Final = "## system/ 카드 줄"
KEY: Final = f"obsidian:{RELPATH}#owner-card"
TITLE: Final = "소유자 카드 (유돌 기억 사본)"
CARD_CHARS: Final = 3000

Group = tuple[str, list[dict[str, Any]]]


def section(text: str) -> str:
    kept: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.strip() == SECTION:
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside:
            kept.append(line)
    return "\n".join(kept).strip()[:CARD_CHARS]


def load(home: Path) -> list[Group]:
    try:
        text = (home / MIRROR / RELPATH).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    body = section(text)
    if not body:
        return []
    row = {"source": KEY, "content": body, "score": 1.0,
           "metadata": {"source_type": "obsidian", "path": RELPATH, "title": TITLE}}
    return [(KEY, [row])]
