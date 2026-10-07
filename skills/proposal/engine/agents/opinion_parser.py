from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from ..contracts.collab_models import ReviewerOpinion

_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^## (?P<locator>\S+)\s*$")
_SUBHEADING_RE: Final[re.Pattern[str]] = re.compile(r"^### (?P<name>\S+)\s*$")
_LOCATOR_RE: Final[re.Pattern[str]] = re.compile(r"^(?:sec\d+(?:\.claim\d+)?|ir\.[a-z_]+)$")


def parse_opinion_dir(path: Path) -> list[ReviewerOpinion]:
    opinions: list[ReviewerOpinion] = []
    for opinion_file in sorted(path.glob("*.md")):
        opinions.extend(parse_opinion_file(opinion_file))
    return sorted(opinions, key=lambda opinion: (opinion.reviewer_id, opinion.locator))


def parse_opinion_file(path: Path) -> list[ReviewerOpinion]:
    text = path.read_text(encoding="utf-8")
    reviewer_id, body = _split_frontmatter(text)
    blocks = _split_blocks(body)
    return [
        ReviewerOpinion(
            reviewer_id=reviewer_id,
            locator=block.locator,
            opinion_text=block.opinion_text,
            proposed_edit=block.proposed_edit,
            source_ids=block.source_ids,
        )
        for block in blocks
    ]


class _OpinionBlock:
    def __init__(
        self,
        locator: str,
        opinion_text: str,
        proposed_edit: str | None,
        source_ids: tuple[str, ...] | None,
    ) -> None:
        self.locator = locator
        self.opinion_text = opinion_text
        self.proposed_edit = proposed_edit
        self.source_ids = source_ids


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("reviewer_id is required in frontmatter")
    frontmatter, _, body = text[4:].partition("\n---")
    reviewer_id = ""
    for line in frontmatter.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "reviewer_id":
            reviewer_id = value.strip()
            break
    if not reviewer_id:
        raise ValueError("reviewer_id is required in frontmatter")
    return reviewer_id, body.lstrip("\n")


def _split_blocks(body: str) -> list[_OpinionBlock]:
    blocks: list[_OpinionBlock] = []
    current_locator = ""
    current_lines: list[str] = []
    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        if match is not None:
            if current_locator:
                blocks.append(_parse_block(current_locator, current_lines))
            current_locator = match.group("locator")
            current_lines = []
            continue
        current_lines.append(line)
    if current_locator:
        blocks.append(_parse_block(current_locator, current_lines))
    return blocks


def _parse_block(locator: str, lines: list[str]) -> _OpinionBlock:
    if _LOCATOR_RE.match(locator) is None:
        raise ValueError(f"Invalid locator: {locator}")

    opinion_lines: list[str] = []
    proposed_lines: list[str] = []
    source_ids: list[str] = []
    section = "opinion"
    for line in lines:
        match = _SUBHEADING_RE.match(line)
        if match is not None:
            section = match.group("name")
            continue
        if section == "proposed_edit":
            proposed_lines.append(line)
        elif section == "source_ids":
            stripped = line.strip()
            if stripped.startswith("-"):
                source_ids.append(stripped.removeprefix("-").strip())
        else:
            opinion_lines.append(line)

    proposed_edit = _joined_text(proposed_lines)
    return _OpinionBlock(
        locator=locator,
        opinion_text=_joined_text(opinion_lines) or "",
        proposed_edit=proposed_edit,
        source_ids=tuple(source_ids) if source_ids else None,
    )


def _joined_text(lines: list[str]) -> str | None:
    text = "\n".join(line.rstrip() for line in lines).strip()
    return text or None


__all__ = ["parse_opinion_dir", "parse_opinion_file"]
