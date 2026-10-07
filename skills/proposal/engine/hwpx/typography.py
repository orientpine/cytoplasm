from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "BODY",
    "COMPACT",
    "FORM_CHAR_STYLES",
    "FormCharStyle",
    "MAJOR_HEADING",
    "SEED_CHAR_PROPERTY_COUNT",
    "SUB_HEADING",
    "BULLET_INDENT_PARAGRAPHS",
    "BULLET_INDENT_STEP",
    "FIGURE_CENTER_PARAGRAPH",
    "HEADING_KEEP_PARAGRAPH",
    "SEED_PARAGRAPH_PROPERTY_COUNT",
    "TypographyError",
    "apply_compact_table_runs",
    "declares_form_typography",
    "install_form_typography",
]


class TypographyError(RuntimeError):
    """The header cannot carry the form's character properties."""


@dataclass(frozen=True, slots=True)
class FormCharStyle:
    char_id: str
    height: int
    bold: bool


# The form states its own typesetting rule twice — in its opening guidance
# paragraph and in resource/rule.md: "A4 / 돋움 11pt / 줄간격 160%". The seed's
# paraPr already carries the 160% line spacing, but every one of its charPr
# entries points at 함초롬바탕 at 10pt, so a render that reuses them comes out in
# the wrong face at the wrong size no matter how faithful the structure is.
# 함초롬돋움 is HANGUL font id 0 in the seed header, and ids 0..6 are taken, so
# these four start at 7.
SEED_CHAR_PROPERTY_COUNT: Final = 7
BODY: Final = FormCharStyle("7", 1100, False)
SUB_HEADING: Final = FormCharStyle("8", 1100, True)
MAJOR_HEADING: Final = FormCharStyle("9", 1400, True)
COMPACT: Final = FormCharStyle("10", 1000, False)
FORM_CHAR_STYLES: Final = (BODY, SUB_HEADING, MAJOR_HEADING, COMPACT)

# 개조식 계층은 기호만이 아니라 들여쓰기로도 구분된다. 7mm 단계는 정부·공공 문서에서
# 반복되는 관행값이고, 시드는 paraPr 0..20 을 쓰므로 새 항목은 21 부터 시작한다.
SEED_PARAGRAPH_PROPERTY_COUNT: Final = 21
BULLET_INDENT_STEP: Final = 1_984
BULLET_INDENT_PARAGRAPHS: Final = ("21", "22", "23", "24")
# A heading alone at the foot of a page, with the content it introduces overleaf,
# is the whitespace a reader notices first. 바탕글(paraPr 0) with keepWithNext.
HEADING_KEEP_PARAGRAPH: Final = "25"
# 그림은 인라인(treatAsChar=1)이라 가로 위치는 그림을 안은 문단의 정렬이 정한다
# (소유자 지시 2026-08-28). 2026-08-30~09-30 의 float 는 쪽 끝에서 본문을 덮어
# 되돌렸다. keepWithNext 는 그림 줄과 바로 다음 캡션 문단을 한 쪽에 붙든다.
FIGURE_CENTER_PARAGRAPH: Final = "26"

_DOTUM_FONT_REF: Final = (
    b'<hh:fontRef hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
)
_BOLD_ELEMENT: Final = b"<hh:bold/>"
_CHAR_PROPERTIES_OPEN_RE: Final = re.compile(rb'<hh:charProperties itemCnt="(\d+)">')
_CHAR_PROPERTIES_CLOSE: Final = b"</hh:charProperties>"
_TEMPLATE_RE: Final = re.compile(rb'<hh:charPr id="0"[ >].*?</hh:charPr>', re.DOTALL)
_FONT_REF_RE: Final = re.compile(rb"<hh:fontRef\b[^>]*/>")
_UNDERLINE_START: Final = b"<hh:underline"
_PARA_PROPERTIES_OPEN_RE: Final = re.compile(rb'<hh:paraProperties itemCnt="(\d+)">')
_PARA_PROPERTIES_CLOSE: Final = b"</hh:paraProperties>"
_PARA_TEMPLATE_RE: Final = re.compile(rb'<hh:paraPr id="1"[ >].*?</hh:paraPr>', re.DOTALL)
_PARA_LEFT_RE: Final = re.compile(rb'<hc:left value="\d+" unit="HWPUNIT"/>')
_HEADING_TEMPLATE_RE: Final = re.compile(rb'<hh:paraPr id="0"[ >].*?</hh:paraPr>', re.DOTALL)
_KEEP_WITH_NEXT_RE: Final = re.compile(rb'\bkeepWithNext="0"')
_TABLE_BOUNDARY_RE: Final = re.compile(rb"<hp:tbl\b|</hp:tbl>")
_RUN_OPEN: Final = b"<hp:run "
_RUN_CHAR_REF_RE: Final = re.compile(rb'(<hp:run charPrIDRef=")(\d+)(")')


def install_form_typography(header_bytes: bytes) -> bytes:
    """Append the form's character properties to the rendered header."""
    open_match = _CHAR_PROPERTIES_OPEN_RE.search(header_bytes)
    if open_match is None:
        raise TypographyError("header declares no <hh:charProperties itemCnt>")
    declared = int(open_match.group(1))
    if declared != SEED_CHAR_PROPERTY_COUNT:
        raise TypographyError(
            f"header declares {declared} charPr entries, expected {SEED_CHAR_PROPERTY_COUNT};"
            " the form's character property ids would collide"
        )
    template_match = _TEMPLATE_RE.search(header_bytes)
    if template_match is None:
        raise TypographyError("header has no charPr id=0 to derive the form styles from")

    appended = b"".join(_char_property(template_match.group(0), style) for style in FORM_CHAR_STYLES)
    close_at = header_bytes.find(_CHAR_PROPERTIES_CLOSE, template_match.end())
    if close_at == -1:
        raise TypographyError("header has no </hh:charProperties>")

    total = declared + len(FORM_CHAR_STYLES)
    with_styles = header_bytes[:close_at] + appended + header_bytes[close_at:]
    with_styles = (
        with_styles[: open_match.start()]
        + f'<hh:charProperties itemCnt="{total}">'.encode()
        + with_styles[open_match.end() :]
    )
    return _install_bullet_indents(with_styles)


def _install_bullet_indents(header_bytes: bytes) -> bytes:
    open_match = _PARA_PROPERTIES_OPEN_RE.search(header_bytes)
    if open_match is None:
        raise TypographyError("header declares no <hh:paraProperties itemCnt>")
    declared = int(open_match.group(1))
    if declared != SEED_PARAGRAPH_PROPERTY_COUNT:
        raise TypographyError(
            f"header declares {declared} paraPr entries, expected"
            f" {SEED_PARAGRAPH_PROPERTY_COUNT}; the bullet indent ids would collide"
        )
    template_match = _PARA_TEMPLATE_RE.search(header_bytes)
    if template_match is None:
        raise TypographyError("header has no paraPr id=1 to derive bullet indents from")

    heading_template = _HEADING_TEMPLATE_RE.search(header_bytes)
    if heading_template is None:
        raise TypographyError("header has no paraPr id=0 to derive the heading shape from")
    appended = (
        b"".join(
            _indented_paragraph(template_match.group(0), para_id, level)
            for level, para_id in enumerate(BULLET_INDENT_PARAGRAPHS)
        )
        + _kept_with_next(heading_template.group(0))
        + _centered_figure_paragraph(heading_template.group(0))
    )
    close_at = header_bytes.find(_PARA_PROPERTIES_CLOSE, template_match.end())
    if close_at == -1:
        raise TypographyError("header has no </hh:paraProperties>")
    total = declared + len(BULLET_INDENT_PARAGRAPHS) + 2
    with_indents = header_bytes[:close_at] + appended + header_bytes[close_at:]
    return (
        with_indents[: open_match.start()]
        + f'<hh:paraProperties itemCnt="{total}">'.encode()
        + with_indents[open_match.end() :]
    )


def _kept_with_next(template: bytes) -> bytes:
    head_end = template.find(b">")
    head = re.sub(
        rb'\bid="0"', f'id="{HEADING_KEEP_PARAGRAPH}"'.encode(), template[:head_end], count=1
    )
    return head + _KEEP_WITH_NEXT_RE.sub(b'keepWithNext="1"', template[head_end:], count=1)


def _centered_figure_paragraph(template: bytes) -> bytes:
    head_end = template.find(b">")
    head = re.sub(
        rb'\bid="0"', f'id="{FIGURE_CENTER_PARAGRAPH}"'.encode(), template[:head_end], count=1
    )
    body = template[head_end:].replace(b'horizontal="JUSTIFY"', b'horizontal="CENTER"', 1)
    if b'horizontal="CENTER"' not in body:
        raise TypographyError("paraPr template has no JUSTIFY alignment to centre")
    return head + _KEEP_WITH_NEXT_RE.sub(b'keepWithNext="1"', body, count=1)


def _indented_paragraph(template: bytes, para_id: str, level: int) -> bytes:
    head_end = template.find(b">")
    head = re.sub(rb'\bid="1"', f'id="{para_id}"'.encode(), template[:head_end], count=1)
    left = f'<hc:left value="{level * BULLET_INDENT_STEP}" unit="HWPUNIT"/>'.encode()
    body = _PARA_LEFT_RE.sub(left, template[head_end:])
    return head + body


def declares_form_typography(header_bytes: bytes) -> bool:
    """Whether this header already carries the form's character properties.

    ``embed_images`` runs on documents that never went through the render path —
    fixtures and standalone calls — and a caption pointing at a charPr the header
    does not declare is a dangling reference Hangul resolves however it likes.
    """
    return all(
        f'<hh:charPr id="{style.char_id}"'.encode() in header_bytes
        for style in FORM_CHAR_STYLES
    )


def apply_compact_table_runs(section_bytes: bytes) -> bytes:
    """Point every run of a table — and the run that wraps it — at the compact style.

    Tables carry their own typographic needs — the seed's 13-column gantt is laid
    out for 10pt — so they take the compact style instead of the 11pt body size
    while still moving onto the form's 돋움 face with the rest of the document.

    Spans are found by counting <hp:tbl> depth rather than by walking to the
    enclosing </hp:p>: a table is full of cell paragraphs, so that walk lands
    inside the NEXT table whenever two tables sit back to back, and every cell
    after the first keeps the seed's face.
    """
    pieces: list[bytes] = []
    cursor = 0
    for start, end in _table_spans(section_bytes):
        wrapped = _wrapping_run_start(section_bytes, start, cursor)
        pieces.append(section_bytes[cursor:wrapped])
        pieces.append(
            _RUN_CHAR_REF_RE.sub(
                rb"\g<1>" + COMPACT.char_id.encode() + rb"\g<3>",
                section_bytes[wrapped:end],
            )
        )
        cursor = end
    pieces.append(section_bytes[cursor:])
    return b"".join(pieces)


def _table_spans(section_bytes: bytes) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    for match in _TABLE_BOUNDARY_RE.finditer(section_bytes):
        if match.group(0).startswith(b"</"):
            depth = max(depth - 1, 0)
            if depth == 0:
                spans.append((start, match.end()))
        else:
            if depth == 0:
                start = match.start()
            depth += 1
    return spans


def _wrapping_run_start(section_bytes: bytes, table_start: int, floor: int) -> int:
    run_at = section_bytes.rfind(_RUN_OPEN, floor, table_start)
    if run_at == -1:
        return table_start
    close = section_bytes.find(b">", run_at, table_start)
    return run_at if close == table_start - 1 else table_start


def _char_property(template: bytes, style: FormCharStyle) -> bytes:
    head_end = template.find(b">")
    head = template[:head_end]
    head = re.sub(rb'\bid="0"', f'id="{style.char_id}"'.encode(), head, count=1)
    head = re.sub(rb'\bheight="\d+"', f'height="{style.height}"'.encode(), head, count=1)
    body = _FONT_REF_RE.sub(_DOTUM_FONT_REF, template[head_end:], count=1)
    if style.bold:
        underline_at = body.find(_UNDERLINE_START)
        if underline_at == -1:
            raise TypographyError("charPr template has no <hh:underline> to place <hh:bold> before")
        body = body[:underline_at] + _BOLD_ELEMENT + body[underline_at:]
    return head + body
