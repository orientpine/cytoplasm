from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import cast

_SOURCE_FRONT_MATTER = "---\nsource: kimm-excavator-wiki\n---\n"
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _strip_front_matter(text: str) -> str:
    """Drop a leading ``---`` ... ``---`` YAML-like block, returning the body."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1 :]).lstrip("\n")
    return text


def _normalize_wikilinks(text: str) -> str:
    """Convert ``[[target]]`` -> ``target`` and ``[[target|display]]`` -> ``display``."""

    def _replace(match: re.Match[str]) -> str:
        inner = match.group(1)
        return inner.rsplit("|", 1)[-1].strip()

    return _WIKILINK_RE.sub(_replace, text)


def _convert_text(text: str) -> str:
    body = _normalize_wikilinks(_strip_front_matter(text)).strip()
    return f"{_SOURCE_FRONT_MATTER}{body}\n"


def convert_excavator_corpus(
    wiki_dir: str | Path,
    out_dir: str | Path,
) -> list[Path]:
    """Convert the excavator wiki subset to kimm-docbot corpus format.

    Deterministic: inputs are globbed in sorted name order, files whose name
    starts with ``_`` (meta/report files) are excluded, wikilinks are flattened,
    and a ``source: kimm-excavator-wiki`` front-matter is prepended. Output
    filenames mirror the source filename. Returns the sorted output paths.
    """
    wiki_path = Path(wiki_dir)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    sources = sorted(
        (
            item
            for item in wiki_path.glob("*.md")
            if item.is_file() and not item.name.startswith("_")
        ),
        key=lambda item: item.name,
    )

    written: list[Path] = []
    for source in sources:
        content = _convert_text(source.read_text(encoding="utf-8"))
        target = out_path / source.name
        _ = target.write_text(content, encoding="utf-8")
        written.append(target)

    return sorted(written, key=lambda item: item.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m skills.proposal.engine.converter.excavator_convert",
        description="Convert the excavator wiki subset to kimm-docbot corpus files",
    )
    _ = parser.add_argument("wiki_dir", help="Path to the kimm_excavator wiki directory")
    _ = parser.add_argument("out_dir", help="Output corpus directory")
    args = parser.parse_args(argv)

    wiki_dir = Path(cast(str, args.wiki_dir))
    out_dir = Path(cast(str, args.out_dir))

    if not wiki_dir.exists() or not wiki_dir.is_dir():
        print(f"ERROR: wiki directory not found: {wiki_dir}", file=sys.stderr)
        return 1

    written = convert_excavator_corpus(wiki_dir, out_dir)
    if not written:
        print(f"ERROR: no excavator wiki files found in {wiki_dir}", file=sys.stderr)
        return 1

    for path in written:
        print(str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["convert_excavator_corpus", "main"]
