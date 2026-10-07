from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, TypeAlias

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,4})\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^(\s*)([◦–□\-*])\s+(.+?)\s*$")
_NUMBERED_RE = re.compile(r"^(\s*)(\d+\.|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])\s+(.+?)\s*$")
_IMAGE_REF_RE = re.compile(r"^\s*<그림\s+([0-9]+-[0-9]+)\s*:\s*(.*?)>\s*$")
_IMAGE_MD_RE = re.compile(r"^\s*!\[([^]]*)]\(([^)]+)\)\s*$")
_IMAGE_CAPTION_RE = re.compile(r"^\s*\*그림\s+([0-9]+-[0-9]+)\s*:\s*(.*?)\*\s*$")
_INLINE_RE = re.compile(r"\*\*([^*]+)\*\*|\*([^*\n]+)\*|`([^`]+)`")


class MarkdownBlock(Protocol):
    def render_text(self) -> str: ...


@dataclass(frozen=True, slots=True)
class Paragraph:
    text: str

    def render_text(self) -> str:
        return self.text


@dataclass(frozen=True, slots=True)
class Bullet:
    marker: str
    text: str
    indent_level: int

    def render_text(self) -> str:
        return f"{'  ' * self.indent_level}{self.marker} {self.text}"


@dataclass(frozen=True, slots=True)
class NumberedItem:
    marker: str
    text: str
    indent_level: int

    def render_text(self) -> str:
        return f"{'  ' * self.indent_level}{self.marker} {self.text}"


@dataclass(frozen=True, slots=True)
class Subheading:
    level: int
    text: str

    def render_text(self) -> str:
        return self.text


@dataclass(frozen=True, slots=True)
class ImagePlaceholder:
    identifier: str | None = None
    caption: str = ""
    path: str | None = None
    alt: str = ""

    def render_text(self) -> str:
        if self.identifier is not None:
            return f"그림 {self.identifier}: {self.caption}".rstrip()
        label = self.alt or self.path or "이미지"
        return f"그림: {label}"


Block: TypeAlias = Paragraph | Bullet | NumberedItem | Subheading | ImagePlaceholder


def parse_markdown_blocks(content: str) -> list[Block]:
    lines = content.splitlines()
    indent_unit = _indent_unit(lines)
    blocks: list[Block] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        image_ref = _IMAGE_REF_RE.match(line)
        if image_ref is not None:
            blocks.append(
                ImagePlaceholder(
                    identifier=image_ref.group(1),
                    caption=_clean_inline(image_ref.group(2)),
                )
            )
            index += 1
            continue

        image_md = _IMAGE_MD_RE.match(line)
        if image_md is not None:
            blocks.append(
                ImagePlaceholder(
                    path=image_md.group(2).strip(),
                    alt=_clean_inline(image_md.group(1)),
                )
            )
            index += 1
            continue

        image_caption = _IMAGE_CAPTION_RE.match(line)
        if image_caption is not None:
            blocks.append(
                ImagePlaceholder(
                    identifier=image_caption.group(1),
                    caption=_clean_inline(image_caption.group(2)),
                )
            )
            index += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading is not None:
            blocks.append(Subheading(level=len(heading.group(1)), text=_clean_inline(heading.group(2))))
            index += 1
            continue

        bullet = _BULLET_RE.match(line)
        if bullet is not None:
            text, index = _collect_block_text(lines, index, bullet.group(3))
            blocks.append(
                Bullet(
                    marker=bullet.group(2),
                    text=_clean_inline(text),
                    indent_level=_indent_level(bullet.group(1), indent_unit),
                )
            )
            continue

        numbered = _NUMBERED_RE.match(line)
        if numbered is not None:
            text, index = _collect_block_text(lines, index, numbered.group(3))
            blocks.append(
                NumberedItem(
                    marker=numbered.group(2),
                    text=_clean_inline(text),
                    indent_level=_indent_level(numbered.group(1), indent_unit),
                )
            )
            continue

        text, index = _collect_block_text(lines, index, line.strip())
        blocks.append(Paragraph(text=_clean_inline(text)))

    return blocks


def _collect_block_text(lines: list[str], index: int, first: str) -> tuple[str, int]:
    parts = [first.strip()]
    index += 1
    while index < len(lines):
        line = lines[index]
        if not line.strip() or _starts_block(line):
            break
        parts.append(line.strip())
        index += 1
    return " ".join(parts), index


def _starts_block(line: str) -> bool:
    return any(
        pattern.match(line) is not None
        for pattern in (
            _IMAGE_REF_RE,
            _IMAGE_MD_RE,
            _IMAGE_CAPTION_RE,
            _HEADING_RE,
            _BULLET_RE,
            _NUMBERED_RE,
        )
    )


def _indent_unit(lines: list[str]) -> int:
    widths: list[int] = []
    for line in lines:
        match = _BULLET_RE.match(line) or _NUMBERED_RE.match(line)
        if match is not None:
            width = len(match.group(1).expandtabs(4))
            if width:
                widths.append(width)
    return min(widths, default=2)


def _indent_level(leading: str, indent_unit: int) -> int:
    return len(leading.expandtabs(4)) // indent_unit


_CODE_SPAN_RE = re.compile(r"`([^`]+)`")
_STRIKETHROUGH_RE = re.compile(r"~~([^~]+)~~")
_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)\)")


def _link_text(match: re.Match[str]) -> str:
    label = match.group(1).strip()
    url = match.group(2).strip()
    if not label:
        return url
    if label == url:
        return label
    return f"{label} ({url})"


def _strip_non_emphasis(text: str) -> str:
    text = _CODE_SPAN_RE.sub(r"\1", text)
    text = _STRIKETHROUGH_RE.sub(r"\1", text)
    return _LINK_RE.sub(_link_text, text)


def _clean_inline(text: str) -> str:
    text = _strip_non_emphasis(text)
    return _INLINE_RE.sub(lambda match: next(group for group in match.groups() if group is not None), text).strip()


__all__ = [
    "Block",
    "Bullet",
    "ImagePlaceholder",
    "MarkdownBlock",
    "NumberedItem",
    "Paragraph",
    "Subheading",
    "parse_markdown_blocks",
]
