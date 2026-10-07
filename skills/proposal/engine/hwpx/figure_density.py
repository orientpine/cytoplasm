"""Deterministic figure numbering and structural two-page layout bands.

HWPX-to-PDF conversion is not required for the structural gate. A band's figure is
its first payload and its span is computed with :func:`estimate_pages`. The renderer
emits no forced page breaks; read-back still rejects an imported first-band break
that could strand a section heading. Tables exceeding two pages are rejected with
``TABLE_SPAN_EXCEEDED``; row-boundary splitting remains the table producer's job,
and every accepted table must retain ``repeatHeader=1``.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape, unescape
from pathlib import Path
from typing import Final

from ..agents.kimm_domain import CAPTION_SENTENCE_SUFFIX
from ._image_contract import MAX_DISPLAY_HEIGHT
from ..contracts import AnchorMap
from ..contracts.layout_profile import (
    BODY_HEIGHT,
    LayoutSpec,
    SectionLayout,
    estimate_pages,
)
from .band_styles import DEFAULT_BAND_STYLES, BandStyleIds
from .md_blocks import (
    Bullet,
    NumberedItem,
    Subheading,
    parse_markdown_blocks,
)

FIG_TOKEN_RE: Final = re.compile(r"\[\[FIG:([a-z0-9][a-z0-9-]*)\]\]")
DIRECT_FIGURE_NUMBER_RE: Final = re.compile(r"그림\s+\d+")
REFERENCE_RE: Final = re.compile(r"그림\s+(\d+)")
# BODY_HEIGHT is 74,266 HWP units; the top quarter therefore ends at 18,566.
# Inline image anchors normally use zero offsets, while this bound directly enforces
# the density contract without rejecting equivalent top-region placements.
TOP_REGION_MAX_OFFSET: Final = BODY_HEIGHT // 4


class FigureDensityError(ValueError):
    """A named, user-actionable figure layout contract violation."""


@dataclass(frozen=True, slots=True)
class ExternalDensityCheck:
    status: str
    reason: str
    max_imageless_run: int | None = None


@dataclass(frozen=True, slots=True)
class FigureSpec:
    figure_id: str
    section_id: str
    caption: str
    band_index: int


@dataclass(frozen=True, slots=True)
class LayoutBand:
    band_index: int
    section_id: str
    figures: tuple[FigureSpec, ...]
    body: str
    figure_heights: tuple[int, ...] = ()
    table_heights: tuple[int, ...] = ()
    table_repeat_headers: tuple[bool, ...] = ()


@dataclass(frozen=True, slots=True)
class DensityValidation:
    ok: bool
    total_pages: int
    figure_pages: tuple[int, ...]
    caption_numbers: tuple[int, ...]
    reference_numbers: frozenset[int]


def load_figure_specs(path: str | Path, *, expected_band_count: int) -> tuple[FigureSpec, ...]:
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FigureDensityError(f"MALFORMED_FIGURES_MANIFEST: {manifest_path}: {exc}") from exc
    if not isinstance(payload, list):
        raise FigureDensityError("MALFORMED_FIGURES_MANIFEST: top level must be a list")

    figures: list[FigureSpec] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise FigureDensityError(f"MALFORMED_FIGURES_MANIFEST: item {index} must be an object")
        try:
            figure_id = item["figure_id"]
            section_id = item["section_id"]
            caption = item["caption"]
            band_index = item["band_index"]
        except KeyError as exc:
            raise FigureDensityError(
                f"MALFORMED_FIGURES_MANIFEST: item {index} missing {exc.args[0]}"
            ) from exc
        if not isinstance(figure_id, str) or FIG_TOKEN_RE.fullmatch(f"[[FIG:{figure_id}]]") is None:
            raise FigureDensityError(f"MALFORMED_FIGURES_MANIFEST: invalid figure_id {figure_id!r}")
        if not isinstance(section_id, (str, int)) or not str(section_id).isdigit():
            raise FigureDensityError(f"MALFORMED_FIGURES_MANIFEST: invalid section_id for {figure_id}")
        if not isinstance(caption, str) or not caption.strip():
            raise FigureDensityError(f"MALFORMED_FIGURES_MANIFEST: empty caption for {figure_id}")
        if caption.strip().rstrip(".").endswith(CAPTION_SENTENCE_SUFFIX):
            raise FigureDensityError(
                f"CAPTION_NOT_NOUN_PHRASE: {figure_id} caption reads as a sentence; "
                "a 연구개발계획서 labels figures with a noun phrase "
                f"(got {caption.strip()[:40]!r})"
            )
        if not isinstance(band_index, int):
            raise FigureDensityError(f"MALFORMED_FIGURES_MANIFEST: invalid band_index for {figure_id}")
        figures.append(FigureSpec(figure_id, str(section_id), " ".join(caption.split()), band_index))

    ids = [figure.figure_id for figure in figures]
    duplicate_ids = sorted(identifier for identifier in set(ids) if ids.count(identifier) > 1)
    if duplicate_ids:
        raise FigureDensityError(f"DUPLICATE_FIGURE_ID: {', '.join(duplicate_ids)}")
    for figure in figures:
        if not 0 <= figure.band_index < expected_band_count:
            raise FigureDensityError(
                f"BAND_INDEX_OUT_OF_RANGE: {figure.figure_id} uses {figure.band_index}; "
                f"expected 0..{expected_band_count - 1}"
            )
    by_band: dict[int, list[str]] = {}
    for figure in figures:
        by_band.setdefault(figure.band_index, []).append(figure.figure_id)
    duplicates = [(band, ids) for band, ids in by_band.items() if len(ids) > 1]
    if duplicates:
        band, identifiers = sorted(duplicates)[0]
        raise FigureDensityError(
            f"DUPLICATE_BAND_ASSIGNMENT: band {band}: {', '.join(identifiers)}"
        )
    missing = sorted(set(range(expected_band_count)) - set(by_band))
    if missing:
        raise FigureDensityError(f"MISSING_BAND_ASSIGNMENT: {', '.join(map(str, missing))}")
    return tuple(figures)


def resolve_figure_tokens(
    text: str, figures: Sequence[FigureSpec]
) -> tuple[str, dict[str, int]]:
    by_id = {figure.figure_id: figure for figure in figures}
    mapping: dict[str, int] = {}
    unknown: list[str] = []
    for match in FIG_TOKEN_RE.finditer(text):
        identifier = match.group(1)
        if identifier not in by_id:
            if identifier not in unknown:
                unknown.append(identifier)
        elif identifier not in mapping:
            mapping[identifier] = len(mapping) + 1
    if unknown:
        raise FigureDensityError(f"UNKNOWN_FIGURE_TOKEN: {', '.join(unknown)}")
    for figure in figures:
        if figure.figure_id not in mapping:
            mapping[figure.figure_id] = len(mapping) + 1
    return FIG_TOKEN_RE.sub(lambda match: f"그림 {mapping[match.group(1)]}", text), mapping


def build_layout_bands(
    section_bodies: Mapping[str, str],
    figures: Sequence[FigureSpec],
    *,
    expected_band_count: int,
) -> tuple[tuple[LayoutBand, ...], dict[str, int]]:
    """Split section paragraphs among one-figure bands in final band order."""
    ordered = tuple(sorted(figures, key=lambda figure: figure.band_index))
    if len(ordered) != expected_band_count:
        raise FigureDensityError(
            f"FIGURE_COUNT: expected {expected_band_count}, found {len(ordered)}"
        )
    if [figure.band_index for figure in ordered] != list(range(expected_band_count)):
        raise FigureDensityError("BAND_ASSIGNMENT: band indexes must be contiguous 0..N-1")
    if any(DIRECT_FIGURE_NUMBER_RE.search(body) for body in section_bodies.values()):
        raise FigureDensityError("DIRECT_FIGURE_NUMBER: use [[FIG:figure-id]] in source bodies")

    known_ids = {figure.figure_id for figure in ordered}
    all_tokens = [
        (section_id, match.group(1))
        for section_id, body in section_bodies.items()
        for match in FIG_TOKEN_RE.finditer(body)
    ]
    unknown = sorted({identifier for _, identifier in all_tokens if identifier not in known_ids})
    if unknown:
        raise FigureDensityError(f"UNKNOWN_FIGURE_TOKEN: {', '.join(unknown)}")

    references_by_section = {
        section_id: {match.group(1) for match in FIG_TOKEN_RE.finditer(body)}
        for section_id, body in section_bodies.items()
    }
    for figure in ordered:
        if figure.figure_id not in references_by_section.get(figure.section_id, set()):
            raise FigureDensityError(f"UNREFERENCED_FIGURE: {figure.figure_id} in section {figure.section_id}")

    assembled_text = "\n\n".join(
        section_bodies.get(section_id, "")
        for section_id in sorted({figure.section_id for figure in ordered}, key=int)
    )
    _, mapping = resolve_figure_tokens(assembled_text, ordered)
    reference_order = [
        figure_id for figure_id, _ in sorted(mapping.items(), key=lambda item: item[1])
    ]
    band_order = [figure.figure_id for figure in ordered]
    caption_sequence = [mapping[figure_id] for figure_id in band_order]
    if caption_sequence != list(range(1, expected_band_count + 1)):
        raise FigureDensityError(
            f"CAPTION_SEQUENCE: reference_order={reference_order} band_order={band_order}"
        )

    bands: list[LayoutBand] = []
    for section_id in sorted({figure.section_id for figure in ordered}, key=int):
        section_figures = [figure for figure in ordered if figure.section_id == section_id]
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", section_bodies.get(section_id, "")) if part.strip()]
        assigned: list[list[str]] = [[] for _ in section_figures]
        figure_positions = {figure.figure_id: index for index, figure in enumerate(section_figures)}
        cursor = 0
        for paragraph in paragraphs:
            paragraph_ids = [match.group(1) for match in FIG_TOKEN_RE.finditer(paragraph)]
            local_positions = [figure_positions[item] for item in paragraph_ids if item in figure_positions]
            if local_positions:
                cursor = min(local_positions)
            assigned[min(cursor, len(assigned) - 1)].append(paragraph)
        for figure, parts in zip(section_figures, assigned, strict=True):
            raw_body = "\n\n".join(parts)
            resolved = FIG_TOKEN_RE.sub(lambda match: f"그림 {mapping[match.group(1)]}", raw_body)
            bands.append(
                LayoutBand(
                    band_index=figure.band_index,
                    section_id=section_id,
                    figures=(figure,),
                    body=resolved,
                    figure_heights=(MAX_DISPLAY_HEIGHT,),
                )
            )
    return tuple(sorted(bands, key=lambda band: band.band_index)), mapping


def _band_span(band: LayoutBand) -> int:
    for index, height in enumerate(band.table_heights):
        repeat_header = (
            band.table_repeat_headers[index]
            if index < len(band.table_repeat_headers)
            else False
        )
        if height > BODY_HEIGHT * 2:
            detail = "repeatHeader=1" if repeat_header else "repeatHeader=0"
            raise FigureDensityError(
                f"TABLE_SPAN_EXCEEDED: band {band.band_index} table {index} exceeds 2 pages ({detail}); "
                "split at a row boundary before rendering"
            )
        if not repeat_header:
            raise FigureDensityError(
                f"TABLE_REPEAT_HEADER_REQUIRED: band {band.band_index} table {index} must use repeatHeader=1"
            )
    pages = estimate_pages(
        LayoutSpec(
            sections=(
                SectionLayout(
                    prose_chars=len(band.body.replace("\n", "")),
                    figure_heights=band.figure_heights,
                    table_heights=band.table_heights,
                ),
            )
        )
    )
    return max(1, math.ceil(pages))


def validate_layout_bands(bands: Sequence[LayoutBand]) -> DensityValidation:
    if not bands:
        raise FigureDensityError("LAYOUT_BANDS_MISSING")
    ordered = tuple(sorted(bands, key=lambda band: band.band_index))
    if [band.band_index for band in ordered] != list(range(len(ordered))):
        raise FigureDensityError("BAND_ORDER: band indexes must be contiguous 0..N-1")

    figure_pages: list[int] = []
    caption_numbers: list[int] = []
    references: set[int] = set()
    page = 1
    figure_ids: list[str] = []
    for number, band in enumerate(ordered, start=1):
        if len(band.figures) != 1:
            raise FigureDensityError(
                f"IMAGE_FREE_RUN: band {band.band_index} has {len(band.figures)} figures; expected exactly 1"
            )
        span = _band_span(band)
        if span > 2:
            raise FigureDensityError(
                f"BAND_SPAN_EXCEEDED: band {band.band_index} spans {span} pages; maximum is 2"
            )
        figure = band.figures[0]
        if figure.section_id != band.section_id:
            raise FigureDensityError(
                f"FIGURE_SECTION_MISMATCH: {figure.figure_id} is not in section {band.section_id}"
            )
        figure_ids.append(figure.figure_id)
        figure_pages.append(page)
        caption_numbers.append(number)
        references.update(int(value) for value in REFERENCE_RE.findall(band.body))
        page += span

    total_pages = page - 1
    expected = set(caption_numbers)
    if references != expected:
        missing = sorted(expected - references)
        if missing:
            identifiers = [figure_ids[number - 1] for number in missing]
            raise FigureDensityError(f"UNREFERENCED_FIGURE: {', '.join(identifiers)}")
        raise FigureDensityError(
            f"REFERENCE_CAPTION_MISMATCH: references={sorted(references)} captions={sorted(expected)}"
        )
    if figure_pages[0] - 1 > 1:
        raise FigureDensityError("IMAGE_FREE_RUN: pages before first figure exceed 1")
    if any(current - previous > 2 for previous, current in zip(figure_pages, figure_pages[1:])):
        raise FigureDensityError("IMAGE_FREE_RUN: gap between figures exceeds 2 pages")
    if total_pages - figure_pages[-1] > 1:
        raise FigureDensityError("IMAGE_FREE_RUN: pages after last figure exceed 1")
    return DensityValidation(
        ok=True,
        total_pages=total_pages,
        figure_pages=tuple(figure_pages),
        caption_numbers=tuple(caption_numbers),
        reference_numbers=frozenset(references),
    )


def structural_page_map(bands: Sequence[LayoutBand]) -> dict[int, tuple[str, ...]]:
    validation = validate_layout_bands(bands)
    result: dict[int, tuple[str, ...]] = {
        page: () for page in range(1, validation.total_pages + 1)
    }
    ordered = sorted(bands, key=lambda band: band.band_index)
    for page, band in zip(validation.figure_pages, ordered, strict=True):
        result[page] = tuple(figure.figure_id for figure in band.figures)
    return result


def _band_paragraph(char_id: str, para_id: str, style_id: str, text: str) -> str:
    return (
        f'<hp:p id="0" paraPrIDRef="{para_id}" styleIDRef="{style_id}" pageBreak="0" '
        f'columnBreak="0" merged="0"><hp:run charPrIDRef="{char_id}"><hp:t>'
        f"{escape(text, quote=False)}"
        "</hp:t></hp:run></hp:p>"
    )


def _band_body_paragraphs(body: str, styles: BandStyleIds) -> list[str]:
    paragraphs: list[str] = []
    for block in parse_markdown_blocks(body):
        if isinstance(block, Subheading):
            text = block.text
            char_id, para_id = styles.heading_char(block.level), styles.heading_para
            style_id = styles.heading_style
        elif isinstance(block, Bullet):
            text = f"{styles.bullet_glyph(block.indent_level)} {block.text}"
            char_id = styles.body_char
            para_id = styles.bullet_para_for(block.indent_level)
            style_id = styles.bullet_style
        elif isinstance(block, NumberedItem):
            text = f"{block.marker} {block.text}"
            char_id = styles.body_char
            para_id = styles.bullet_para_for(block.indent_level)
            style_id = styles.bullet_style
        else:
            text = block.render_text()
            char_id, para_id = styles.body_char, styles.body_para
            style_id = styles.body_style
        stripped = text.strip()
        if stripped:
            paragraphs.append(_band_paragraph(char_id, para_id, style_id, stripped))
    return paragraphs


def render_body_paragraphs(
    xml_bytes: bytes,
    slot: str,
    body: str,
    anchor_map: AnchorMap,
    *,
    styles: BandStyleIds = DEFAULT_BAND_STYLES,
) -> bytes:
    """Insert a section body as form paragraphs, with no figure banding.

    Bands and plain sections must agree on what a markdown block becomes, so both
    go through ``_band_body_paragraphs``. Cloning a neighbouring seed paragraph
    instead — the older path — inherited whatever character property that
    paragraph happened to carry and flattened every authored sub-heading.
    """
    from .xml_writer import insert_paragraph_fragment

    fragments = _band_body_paragraphs(body, styles)
    if not fragments:
        return xml_bytes
    return insert_paragraph_fragment(xml_bytes, slot, "".join(fragments).encode("utf-8"), anchor_map)


def render_layout_bands(
    xml_bytes: bytes,
    slot: str,
    bands: Sequence[LayoutBand],
    number_by_id: Mapping[str, int],
    anchor_map: AnchorMap,
    *,
    styles: BandStyleIds = DEFAULT_BAND_STYLES,
) -> bytes:
    """Insert marked band XML without breaks; image comments feed ``image_embed``."""
    from .xml_writer import insert_paragraph_fragment

    fragments: list[str] = []
    for band in sorted(bands, key=lambda item: item.band_index):
        if len(band.figures) != 1:
            raise FigureDensityError(
                f"IMAGE_FREE_RUN: band {band.band_index} has {len(band.figures)} figures; expected exactly 1"
            )
        figure = band.figures[0]
        number = number_by_id[figure.figure_id]
        fragments.append(
            f'<!--LAYOUT-BAND index="{band.band_index}" section="{band.section_id}" '
            f'figure="{figure.figure_id}" number="{number}">-->'
            f'<!--IMAGE:image{number}-->'
        )
        fragments.extend(_band_body_paragraphs(band.body, styles))
        fragments.append(f'<!--/LAYOUT-BAND index="{band.band_index}"-->')
    # The orchestrator processes sections in reverse order, so one insertion preserves
    # both band and figure order inside the anchored section.
    return insert_paragraph_fragment(
        xml_bytes,
        slot,
        "".join(fragments).encode("utf-8"),
        anchor_map,
    )


_BAND_RE: Final = re.compile(
    rb'<!--LAYOUT-BAND index="(\d+)" section="(\d+)" figure="([a-z0-9-]+)" number="(\d+)">-->(.*?)<!--/LAYOUT-BAND index="\1"-->',
    re.DOTALL,
)
# 캡션은 그림 문단 바로 다음의 한 줄 문단이다(image_embed._picture_fragment).
_CAPTION_BYTES_RE: Final = re.compile(
    rb"(?<=</hp:pic></hp:run></hp:p>)<hp:p\b[^>]*><hp:run\b[^>]*><hp:t>\s*"
    + "그림".encode()
    + rb"\s+(\d+)\.\s+[^<]*</hp:t></hp:run></hp:p>"
)


_soffice_probe: tuple[bool, str] | None = None


def check_pdf_image_density(
    source_path: str | Path,
    *,
    seed_path: str | Path,
) -> ExternalDensityCheck:
    """Optionally verify rendered pages with pdfimages, or explicitly return SKIP.

    The seed conversion is attempted once per process and cached. A failed probe is
    never reported as a pass, which keeps unsupported workstations honest.
    """
    global _soffice_probe
    soffice = shutil.which("soffice")
    pdfimages = shutil.which("pdfimages")
    pdfinfo = shutil.which("pdfinfo")
    if soffice is None:
        return ExternalDensityCheck("SKIP", "soffice unavailable")
    if pdfimages is None or pdfinfo is None:
        return ExternalDensityCheck("SKIP", "pdfimages/pdfinfo unavailable")

    if _soffice_probe is None:
        with tempfile.TemporaryDirectory(prefix="kimm-density-probe-") as directory:
            command = [soffice, "--headless", "--convert-to", "pdf", "--outdir", directory, str(seed_path)]
            try:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
                pdf = Path(directory) / f"{Path(seed_path).stem}.pdf"
                if completed.returncode != 0 or not pdf.is_file():
                    detail = (completed.stderr or completed.stdout).strip().replace("\n", " ")
                    _soffice_probe = (False, f"seed HWPX conversion unavailable: rc={completed.returncode} {detail}".strip())
                else:
                    _soffice_probe = (True, "seed HWPX conversion succeeded")
            except (OSError, subprocess.TimeoutExpired) as exc:
                _soffice_probe = (False, f"seed HWPX conversion unavailable: {exc}")
    available, reason = _soffice_probe
    if not available:
        return ExternalDensityCheck("SKIP", reason)

    with tempfile.TemporaryDirectory(prefix="kimm-density-check-") as directory:
        completed = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", directory, str(source_path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        pdf = Path(directory) / f"{Path(source_path).stem}.pdf"
        if completed.returncode != 0 or not pdf.is_file():
            detail = (completed.stderr or completed.stdout).strip().replace("\n", " ")
            return ExternalDensityCheck("SKIP", f"artifact conversion unavailable: rc={completed.returncode} {detail}".strip())
        info = subprocess.run([pdfinfo, str(pdf)], capture_output=True, text=True, check=False)
        page_match = re.search(r"^Pages:\s+(\d+)$", info.stdout, re.MULTILINE)
        if info.returncode != 0 or page_match is None:
            return ExternalDensityCheck("SKIP", "pdfinfo could not read converted artifact")
        pages = int(page_match.group(1))
        current_run = maximum_run = 0
        for page in range(1, pages + 1):
            listed = subprocess.run(
                [pdfimages, "-list", "-f", str(page), "-l", str(page), str(pdf)],
                capture_output=True,
                text=True,
                check=False,
            )
            if listed.returncode != 0:
                return ExternalDensityCheck("SKIP", f"pdfimages failed on page {page}")
            has_image = any(re.match(r"^\s*\d+\s+", line) for line in listed.stdout.splitlines())
            current_run = 0 if has_image else current_run + 1
            maximum_run = max(maximum_run, current_run)
        if maximum_run > 1:
            return ExternalDensityCheck("FAIL", f"IMAGE_FREE_RUN={maximum_run}", maximum_run)
        return ExternalDensityCheck("PASS", "pdfimages structural density passed", maximum_run)


def validate_density_xml(section_xml: bytes) -> list[str]:
    """Read back marked bands after image embedding and return named violations."""
    matches = list(_BAND_RE.finditer(section_xml))
    if not matches:
        return []
    errors: list[str] = []
    indexes = [int(match.group(1)) for match in matches]
    if indexes != list(range(len(matches))):
        errors.append(f"BAND_ORDER: {indexes} != {list(range(len(matches)))}")

    figure_pages: list[int] = []
    spans: list[int] = []
    caption_numbers: list[int] = []
    references_by_section: dict[str, set[int]] = {}
    figures_by_section: dict[str, list[tuple[str, int]]] = {}
    opened_sections: set[str] = set()
    current_page = 1
    for match in matches:
        index = int(match.group(1))
        section_id = match.group(2).decode("ascii")
        figure_id = match.group(3).decode("ascii")
        declared_number = int(match.group(4))
        content = match.group(5)
        # The renderer never emits a forced break. Read-back still handles an
        # imported or mutated artifact and names a first-band break explicitly:
        # it could leave the section heading alone on the preceding page.
        leading = re.match(rb'\s*<hp:p\b[^>]*\bpageBreak="1"[^>]*>.*?</hp:p>', content, re.DOTALL)
        if section_id not in opened_sections:
            opened_sections.add(section_id)
            if leading is not None:
                errors.append(
                    f"SECTION_HEADING_ORPHAN: band {index} breaks the page before "
                    f"section {section_id}'s first figure"
                )
        picture_matches = list(re.finditer(rb"<hp:pic\b.*?</hp:pic>", content, re.DOTALL))
        if len(picture_matches) != 1:
            errors.append(
                f"IMAGE_FREE_RUN: band {index} has {len(picture_matches)} figures; expected exactly 1"
            )
        else:
            picture_xml = picture_matches[0].group(0)
            position_match = re.search(rb"<hp:pos\b[^>]*>", picture_xml)
            position_ok = False
            if position_match is not None:
                position_tag = position_match.group(0)
                treat_as_char = _bytes_attribute(position_tag, b"treatAsChar")
                try:
                    vert_offset = int(_bytes_attribute(position_tag, b"vertOffset") or b"")
                    horz_offset = int(_bytes_attribute(position_tag, b"horzOffset") or b"")
                except ValueError:
                    vert_offset = horz_offset = -1
                position_ok = (
                    treat_as_char == b"1"
                    and 0 <= vert_offset <= TOP_REGION_MAX_OFFSET
                    and 0 <= horz_offset <= TOP_REGION_MAX_OFFSET
                )
            if not position_ok:
                errors.append(
                    f"FIGURE_POSITION_OUT_OF_BAND: band {index} requires an inline figure "
                    f"(treatAsChar=1) and offsets within 0..{TOP_REGION_MAX_OFFSET}"
                )
            payload_start = leading.end() if leading is not None else 0
            between = content[payload_start : picture_matches[0].start()]
            if re.search(rb"<hp:(?:t|tbl)\b", between):
                errors.append(f"FIGURE_TOP_QUARTER: band {index} figure is not first payload")

        caption_match = _CAPTION_BYTES_RE.search(content)
        if caption_match is None:
            errors.append(f"CAPTION_MISSING: {figure_id}")
            caption_number = declared_number
        else:
            caption_number = int(caption_match.group(1))
            caption_numbers.append(caption_number)
            if caption_number != declared_number:
                errors.append(
                    f"CAPTION_MAPPING_MISMATCH: {figure_id} declares {declared_number}, got {caption_number}"
                )

        without_captions = _CAPTION_BYTES_RE.sub(b"", content)
        text_payloads = re.findall(rb"<hp:t(?:\s[^>]*)?>(.*?)</hp:t>", without_captions, re.DOTALL)
        body_text = " ".join(
            unescape(re.sub(rb"<[^>]+>", b"", payload).decode("utf-8", "replace"))
            for payload in text_payloads
        )
        references_by_section.setdefault(section_id, set()).update(
            int(value) for value in REFERENCE_RE.findall(body_text)
        )
        figures_by_section.setdefault(section_id, []).append((figure_id, caption_number))

        figure_heights = tuple(
            int(value)
            for value in re.findall(rb"<hp:curSz\b[^>]*\bheight=\"(\d+)\"", content)
        )
        table_heights = tuple(
            int(value)
            for value in re.findall(rb"<hp:tbl\b[^>]*\bheight=\"(\d+)\"", content)
        )
        if any(height > BODY_HEIGHT * 2 for height in table_heights):
            errors.append(f"TABLE_SPAN_EXCEEDED: band {index}; split rows with repeatHeader=1")
        for table_tag in re.findall(rb"<hp:tbl\b[^>]*>", content):
            if b'repeatHeader="1"' not in table_tag:
                errors.append(f"TABLE_REPEAT_HEADER_REQUIRED: band {index}")
        pages = estimate_pages(
            LayoutSpec(
                sections=(
                    SectionLayout(
                        prose_chars=len(body_text.replace("\n", "")),
                        figure_heights=figure_heights,
                        table_heights=table_heights,
                    ),
                )
            )
        )
        span = max(1, math.ceil(pages))
        spans.append(span)
        if span > 2:
            errors.append(f"BAND_SPAN_EXCEEDED: band {index} spans {span} pages")
        figure_pages.append(current_page)
        current_page += span

    expected_captions = list(range(1, len(matches) + 1))
    if caption_numbers != expected_captions:
        errors.append(
            f"CAPTION_SEQUENCE: {caption_numbers} != {expected_captions}"
        )
    all_references = set().union(*references_by_section.values()) if references_by_section else set()
    if all_references != set(expected_captions):
        errors.append(
            f"REFERENCE_CAPTION_MISMATCH: references={sorted(all_references)} captions={expected_captions}"
        )
    for section_id, figures in figures_by_section.items():
        section_references = references_by_section.get(section_id, set())
        for figure_id, number in figures:
            if number not in section_references:
                errors.append(f"UNREFERENCED_FIGURE: {figure_id} in section {section_id}")
    total_pages = current_page - 1
    if figure_pages and (
        figure_pages[0] - 1 > 1
        or any(
            current - previous > 2
            for previous, current in zip(figure_pages, figure_pages[1:])
        )
        or total_pages - figure_pages[-1] > 1
    ):
        errors.append("IMAGE_FREE_RUN: structural page map contains more than one imageless page")
    return errors


def _bytes_attribute(tag: bytes, name: bytes) -> bytes | None:
    match = re.search(rb"\b" + re.escape(name) + rb'=\"([^\"]*)\"', tag)
    return match.group(1) if match is not None else None


__all__ = [
    "FIG_TOKEN_RE",
    "DensityValidation",
    "ExternalDensityCheck",
    "FigureDensityError",
    "FigureSpec",
    "LayoutBand",
    "TOP_REGION_MAX_OFFSET",
    "build_layout_bands",
    "check_pdf_image_density",
    "load_figure_specs",
    "render_layout_bands",
    "resolve_figure_tokens",
    "structural_page_map",
    "validate_density_xml",
    "validate_layout_bands",
]
