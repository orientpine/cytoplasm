"""Place ``[[FIG:…]]`` tokens from ``figures.json`` into the engine-written drafts.

The engine writers never see ``figures.json``, so an engine draft carries no figure
tokens and render refuses it with ``UNREFERENCED_FIGURE`` (2026-09-28 node run). The
excavator reference got its tokens from a proposal-specific augment script; this is the
general form: the author plans figures per section, and this step anchors each one to
the paragraph where its share of the section begins. A writer keeps a claim only when an
evidence fact appears verbatim in its prose, so paraphrased live sections carry none and
traceability scored 0.00; a section left without claims takes its figures' captions and
evidence ids as claims, as the augment script did.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Final, cast

from . import proposal_version
from .proposal_ir import FIG_TOKEN_RE, figures_from_json

STALE_REFINE_OUTPUTS: Final = (
    "drafts.refined.json",
    "drafts.refined.json.planspec.json",
    "drafts.refined.json.pms.json",
    "refine-report.json",
)
PLAN_FIELDS: Final = (
    'figure_id, section_id, source_claim_ids, prompt, caption, png_sha256 ("" until `images`), '
    + "band_index"
)


class FigureTokenError(RuntimeError):
    """Figures cannot be anchored in the current drafts."""


def distribute(blocks: list[str], group_count: int) -> list[list[str]]:
    groups: list[list[str]] = [[] for _ in range(group_count)]
    total = sum(len(block) for block in blocks)
    cumulative = 0
    index = 0
    for position, block in enumerate(blocks):
        if index < group_count - 1 and groups[index]:
            reserved = group_count - index - 1
            filled_share = cumulative >= total * (index + 1) / group_count
            if filled_share or len(blocks) - position <= reserved:
                index += 1
        groups[index].append(block)
        cumulative += len(block)
    return groups


_SENTENCE_END: Final = re.compile(r"(?<![0-9])[.!?。](?=\s|$)")
_NUMBERED_HEADING: Final = re.compile(r"^\s*(?:#+\s*)?\d+(?:-\d+)*\.\s")


def _is_heading(block: str) -> bool:
    stripped = block.lstrip()
    return stripped.startswith("#") or (
        "\n" not in stripped and _NUMBERED_HEADING.match(stripped) is not None
    )


def _cite(block: str, figure_id: str) -> str:
    token = f"[[FIG:{figure_id}]]"
    first, newline, rest = block.partition("\n")
    if newline and _NUMBERED_HEADING.match(first) is not None:
        return f"{first}\n{_cite(rest, figure_id)}"
    match = _SENTENCE_END.search(block)
    if match is None:
        return f"{block} ({token})"
    return f"{block[: match.start()]} ({token}){block[match.start():]}"


def _split_headings(block: str) -> list[str]:
    """Give every markdown heading line its own block so no prose shares it."""
    pieces: list[list[str]] = [[]]
    for line in block.split("\n"):
        if line.lstrip().startswith("#"):
            if pieces[-1]:
                pieces.append([])
            pieces[-1].append(line)
            pieces.append([])
        else:
            pieces[-1].append(line)
    return ["\n".join(piece).strip() for piece in pieces if "\n".join(piece).strip()]


def _without_title_line(body: str, title: str) -> str:
    first, newline, rest = body.partition("\n")
    if newline and title and first.strip() == title.strip() and rest.strip():
        return rest.lstrip("\n")
    return body


def _anchor(group: list[str], figure_id: str, *, cite: bool) -> list[str]:
    for index, block in enumerate(group):
        if not _is_heading(block):
            anchored = _cite(block, figure_id) if cite else f"[[FIG:{figure_id}]] {block}"
            return [*group[:index], anchored, *group[index + 1 :]]
    return [*group, f"[[FIG:{figure_id}]]"]


def fill_body(body: str, figure_ids: list[str], *, cite: bool = True) -> str:
    """Anchor each figure to the first prose block of its share of the section.

    ``cite`` places the token as a closing citation of that block's first sentence
    (``…한다 ([[FIG:x]]).``), the form the refine recast produces; a leading token
    otherwise renders as "그림 N" fused onto the sentence. Headings never carry a
    token — a token before ``### 4-1.`` fused the caption into the heading.
    """
    blocks = [
        piece
        for part in re.split(r"\n\s*\n", body)
        if part.strip()
        for piece in _split_headings(part.strip())
    ]
    if not blocks:
        raise ValueError("section body is empty")
    present = [match.group(1) for block in blocks for match in FIG_TOKEN_RE.finditer(block)]
    if not cite and present == figure_ids:
        return "\n\n".join(blocks)
    blocks = [text for block in blocks if (text := FIG_TOKEN_RE.sub("", block).replace(" ()", "").strip())]
    if not blocks:
        raise ValueError("section body has no prose besides figure tokens")
    paragraphs: list[str] = []
    for figure_id, group in zip(figure_ids, distribute(blocks, len(figure_ids)), strict=True):
        if not group:
            paragraphs.append(f"[[FIG:{figure_id}]]")
            continue
        paragraphs.extend(_anchor(group, figure_id, cite=cite))
    return "\n\n".join(paragraphs)


def place_figures(version: Path) -> dict[str, int]:
    """Anchor every planned figure in its section; return figure counts per section."""
    figures_path = version / "figures.json"
    drafts_path = version / "out" / "drafts.json"
    if figures_path.is_symlink() or not figures_path.is_file():
        raise FigureTokenError("figures.json is missing; plan the figures first")
    if drafts_path.is_symlink() or not drafts_path.is_file():
        raise FigureTokenError("out/drafts.json is missing; run `compose` first")
    try:
        figures = figures_from_json(figures_path.read_text(encoding="utf-8"))
        document = cast(dict[str, object], json.loads(drafts_path.read_text(encoding="utf-8")))
    except KeyError as error:
        raise FigureTokenError(
            f"a figures.json record lacks {error}; every figure needs {PLAN_FIELDS}"
        ) from error
    except (OSError, TypeError, ValueError) as error:
        raise FigureTokenError(f"figures.json or drafts.json is invalid: {error}") from error
    by_section: dict[str, list[str]] = {}
    claims_by_section: dict[str, list[dict[str, object]]] = {}
    for figure in sorted(figures, key=lambda item: item.band_index):
        by_section.setdefault(figure.section_id, []).append(figure.figure_id)
        if figure.source_claim_ids:
            claims_by_section.setdefault(figure.section_id, []).append(
                {"text": figure.caption, "source_ids": list(figure.source_claim_ids)}
            )
    sections = cast(list[dict[str, object]], document.get("sections", []))
    known = {cast(str, section.get("section_id")) for section in sections}
    orphaned = sorted(set(by_section) - known)
    if orphaned:
        raise FigureTokenError(f"figures name sections absent from the drafts: {orphaned}")
    for section in sections:
        section_id = cast(str, section["section_id"])
        ids = by_section.get(section_id)
        if ids:
            body = _without_title_line(cast(str, section["body"]), str(section.get("title", "")))
            section["body"] = fill_body(body, ids)
        if not section.get("claims") and section_id in claims_by_section:
            section["claims"] = claims_by_section[section_id]
    descriptor, name = tempfile.mkstemp(prefix=".drafts.", dir=drafts_path.parent)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        _ = stream.write(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    os.chmod(name, 0o600)
    os.replace(name, drafts_path)
    for stale in STALE_REFINE_OUTPUTS:
        (drafts_path.parent / stale).unlink(missing_ok=True)
    return {section_id: len(ids) for section_id, ids in sorted(by_section.items())}


def command(args: argparse.Namespace) -> int:
    slug = cast(str, args.slug)
    try:
        store = proposal_version.VersionStore.from_environment()
        head = store.head(slug)
        if head is None:
            raise FigureTokenError("proposal has no current version")
        counts = place_figures(store.resolve_slug_dir(slug) / "versions" / head)
    except (FigureTokenError, proposal_version.VersionError) as error:
        print(f"PROPOSAL-FIGURES-ERROR {error}", file=sys.stderr)
        return 1
    payload = {"figures": counts, "slug": slug, "version": head}
    if cast(bool, args.json):
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"PROPOSAL-FIGURES-PLACED slug={slug} version={head} sections={counts}")
    return 0


__all__ = ["FigureTokenError", "STALE_REFINE_OUTPUTS", "command", "fill_body", "place_figures"]
