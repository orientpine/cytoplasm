from __future__ import annotations

from dataclasses import dataclass

from ..agents.kimm_domain import BULLET_GLYPHS
from .typography import (
    BODY,
    BULLET_INDENT_PARAGRAPHS,
    HEADING_KEEP_PARAGRAPH,
    MAJOR_HEADING,
    SUB_HEADING,
)

__all__ = [
    "DEFAULT_BAND_STYLES",
    "BandStyleIds",
]


@dataclass(frozen=True, slots=True)
class BandStyleIds:
    body_char: str
    body_para: str
    body_style: str
    heading_chars: tuple[str, ...]
    heading_para: str
    heading_style: str
    bullet_para: str
    bullet_paras: tuple[str, ...]
    bullet_style: str
    bullet_glyphs: tuple[str, ...]

    def heading_char(self, level: int) -> str:
        index = min(max(level, 1), len(self.heading_chars)) - 1
        return self.heading_chars[index]

    def bullet_glyph(self, indent_level: int) -> str:
        index = min(max(indent_level, 0), len(self.bullet_glyphs) - 1)
        return self.bullet_glyphs[index]

    def bullet_para_for(self, indent_level: int) -> str:
        """The paragraph property carrying this level's left indent."""
        index = min(max(indent_level, 0), len(self.bullet_paras) - 1)
        return self.bullet_paras[index]


# Paragraph shape is measured from the seed: it writes body text with the 본문
# style (styleIDRef 1 / paraPrIDRef 1) and every heading with 바탕글 (0 / 0).
# The character properties come from the form's stated rule instead, because the
# seed's own charPr 0 is 함초롬바탕 10pt — the face the form tells authors not to use.
DEFAULT_BAND_STYLES = BandStyleIds(
    body_char=BODY.char_id,
    body_para="1",
    body_style="1",
    heading_chars=(
        MAJOR_HEADING.char_id,
        SUB_HEADING.char_id,
        SUB_HEADING.char_id,
        SUB_HEADING.char_id,
    ),
    heading_para=HEADING_KEEP_PARAGRAPH,
    heading_style="0",
    bullet_para="1",
    bullet_paras=BULLET_INDENT_PARAGRAPHS,
    bullet_style="1",
    bullet_glyphs=BULLET_GLYPHS,
)
