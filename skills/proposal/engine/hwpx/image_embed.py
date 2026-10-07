"""Embed captioned PNG images with deterministic OWPML picture anchors.

The picture child order and attributes are derived from the real Hangul-produced
``hp:pic`` in the public ``report-template.hwpx`` reference asset. The committed
``picture_carrier.hwpx`` is currently a reproducible seed-derived fixture; a
canonical carrier saved by Hangul can replace it later without changing this API.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Final
from xml.etree import ElementTree as ET

from ._image_contract import (
    DEFAULT_BODY_WIDTH,
    HWP_UNITS_PER_PIXEL_96_DPI,
    MAX_DISPLAY_HEIGHT,
    MAX_DISPLAY_WIDTH,
    MAX_U32,
    ImageEmbedError,
    png_dimensions,
)
from .png_scale import downscale_png
from .typography import (
    COMPACT,
    FIGURE_CENTER_PARAGRAPH,
    declares_form_typography,
)
from .xml_writer import sync_prv_text
from .zip_surgery import repack, replace_entry, unpack


SECTION0_ENTRY: Final = "Contents/section0.xml"
CONTENT_HPF_ENTRY: Final = "Contents/content.hpf"
HEADER_ENTRY: Final = "Contents/header.xml"
PLACEHOLDER_RE: Final = re.compile(rb"<!--IMAGE:(image\d+)-->")
SECTION_ENTRY_RE: Final = re.compile(r"Contents/section\d+\.xml")
IMAGE_ID_RE: Final = re.compile(r"image\d+")
NUMERIC_ID_RE: Final = re.compile(rb'\b(?:id|instid)="(\d+)"')
Z_ORDER_RE: Final = re.compile(rb'\bzOrder="(-?\d+)"')

OPF_NS: Final = "http://www.idpf.org/2007/opf/"
HP_NS: Final = "http://www.hancom.co.kr/hwpml/2011/paragraph"

NEW_IMAGE_MEDIA_TYPE: Final = "image/png"
HWP_UNITS_PER_INCH: Final = 7_200
MM_PER_INCH: Final = 25.4

# The seed's own 캡션 style (styleIDRef 21 / paraPrIDRef 19): centred-gap spacing
# below the picture. Reusing it keeps figure captions off the 바탕글 heading shape
# they were borrowing, which rendered them as loud as a section title.
CAPTION_STYLE_ID: Final = "21"
CAPTION_PARA_ID: Final = "19"
# figures.json 캡션은 번호 없는 설명이다 — 번호는 여기서 붙인다. 이미 붙어 오면
# 「그림 3. 그림 3. …」 이 되므로 앞머리 번호를 걷어 낸다(2026-09-29 노드 실측).
_LEADING_FIGURE_NUMBER_RE: Final = re.compile(r"^그림\s*\d+\s*[.:]\s*")


def mm_to_hwp_units(mm: int) -> int:
    if mm <= 0:
        raise ImageEmbedError("figure width in mm must be positive")
    return round(mm * HWP_UNITS_PER_INCH / MM_PER_INCH)


@dataclass(frozen=True, slots=True)
class ImageSpec:
    path: str | Path
    caption: str


@dataclass(frozen=True, slots=True)
class _PreparedImage:
    identifier: str
    payload: bytes
    caption: str
    pixel_width: int
    pixel_height: int

    @property
    def href(self) -> str:
        return f"BinData/{self.identifier}.png"


class _StableIdAllocator:
    def __init__(self, occupied: set[int]) -> None:
        self._occupied: set[int] = occupied

    def allocate(self, key: str) -> int:
        candidate = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:4], "big")
        start = candidate
        while candidate in self._occupied:
            candidate = (candidate + 1) & MAX_U32
            if candidate == start:
                raise ImageEmbedError("32-bit picture ID space is exhausted")
        self._occupied.add(candidate)
        return candidate


def embed_images(
    source_path: str | Path,
    output_path: str | Path,
    images: Mapping[str, ImageSpec],
    *,
    max_display_width: int = MAX_DISPLAY_WIDTH,
    downscale_width_mm: int | None = None,
) -> Path:
    """Replace ``<!--IMAGE:imageN-->`` comments and atomically write a linked HWPX.

    Inputs and output must differ. All placeholders, PNG payloads, dimensions, and
    manifest collisions are checked before a temporary ZIP is written. The temporary
    package then passes image-specific read-back validation before it replaces the
    destination.
    """

    if not 0 < max_display_width <= MAX_DISPLAY_WIDTH:
        raise ImageEmbedError(
            f"max_display_width must be between 1 and {MAX_DISPLAY_WIDTH} HWP units"
        )
    source = Path(source_path)
    output = Path(output_path)
    if source.resolve() == output.resolve():
        raise ImageEmbedError("input and output must differ; in-place embedding is refused")
    if not source.is_file():
        raise ImageEmbedError(f"input HWPX does not exist: {source}")

    entries = unpack(str(source))
    if CONTENT_HPF_ENTRY not in entries:
        raise ImageEmbedError(f"missing required ZIP entry: {CONTENT_HPF_ENTRY}")
    if SECTION0_ENTRY not in entries:
        raise ImageEmbedError(f"missing required ZIP entry: {SECTION0_ENTRY}")

    section_names = [name for name in entries if SECTION_ENTRY_RE.fullmatch(name)]
    placeholders = [
        match.group(1).decode("ascii")
        for name in section_names
        for match in PLACEHOLDER_RE.finditer(entries[name])
    ]
    if not placeholders:
        detail = "; input may already be embedded" if any(b"<hp:pic" in entries[name] for name in section_names) else ""
        raise ImageEmbedError(f"no image placeholders found{detail}")

    placeholder_ids = set(placeholders)
    mapping_ids = set(images)
    invalid_ids = sorted(identifier for identifier in mapping_ids if IMAGE_ID_RE.fullmatch(identifier) is None)
    if invalid_ids:
        raise ImageEmbedError(f"invalid image mapping identifiers: {', '.join(invalid_ids)}")
    missing = sorted(placeholder_ids - mapping_ids, key=_image_sort_key)
    if missing:
        raise ImageEmbedError(f"missing image mapping for placeholders: {', '.join(missing)}")
    unused = sorted(mapping_ids - placeholder_ids, key=_image_sort_key)
    if unused:
        raise ImageEmbedError(f"unused image mapping identifiers: {', '.join(unused)}")

    downscale_width_px = (
        None
        if downscale_width_mm is None
        else mm_to_hwp_units(downscale_width_mm) // HWP_UNITS_PER_PIXEL_96_DPI
    )
    prepared = {
        identifier: _prepare_image(identifier, images[identifier], downscale_width_px)
        for identifier in sorted(mapping_ids, key=_image_sort_key)
    }
    content_hpf = entries[CONTENT_HPF_ENTRY]
    _reject_manifest_collisions(content_hpf, entries, prepared)

    occupied_ids = {
        int(match.group(1))
        for name in section_names
        for match in NUMERIC_ID_RE.finditer(entries[name])
    }
    allocator = _StableIdAllocator(occupied_ids)
    caption_char_id = (
        COMPACT.char_id if declares_form_typography(entries[HEADER_ENTRY]) else "0"
    )
    # 그림 문단의 중앙 정렬은 증설된 paraPr 가 실제로 선언된 헤더에서만 가리킨다 —
    # 시드 그대로의 문서에서 26 을 가리키면 한/글이 마음대로 해석하는 매달린 참조가 된다.
    host_para_id = (
        FIGURE_CENTER_PARAGRAPH
        if f'<hh:paraPr id="{FIGURE_CENTER_PARAGRAPH}"'.encode() in entries[HEADER_ENTRY]
        else "0"
    )
    next_z_order = 1 + max(
        (
            int(match.group(1))
            for name in section_names
            for match in Z_ORDER_RE.finditer(entries[name])
        ),
        default=0,
    )

    figure_number = 0
    updated_sections: dict[str, bytes] = {}
    for section_name in section_names:
        body_width = _body_width(entries[section_name])

        def replace_placeholder(match: re.Match[bytes]) -> bytes:
            nonlocal figure_number
            identifier = match.group(1).decode("ascii")
            figure_number += 1
            fragment = _picture_fragment(
                prepared[identifier],
                figure_number=figure_number,
                z_order=next_z_order + figure_number - 1,
                body_width=body_width,
                max_display_width=max_display_width,
                allocator=allocator,
                caption_char_id=caption_char_id,
                host_para_id=host_para_id,
            )
            return fragment.encode("utf-8")

        updated_sections[section_name] = PLACEHOLDER_RE.sub(
            replace_placeholder,
            entries[section_name],
        )

    content_hpf = _add_manifest_items(content_hpf, prepared)
    for section_name, section_bytes in updated_sections.items():
        entries = replace_entry(entries, section_name, section_bytes)
    entries = replace_entry(entries, CONTENT_HPF_ENTRY, content_hpf)
    for identifier in sorted(prepared, key=_image_sort_key):
        item = prepared[identifier]
        entries[item.href] = item.payload
    entries = sync_prv_text(
        entries[SECTION0_ENTRY],
        entries,
        updated_sections[SECTION0_ENTRY],
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        repack(entries, str(temporary))
        from .validate import validate_image_links

        readback_errors = validate_image_links(temporary)
        if readback_errors:
            details = "; ".join(readback_errors)
            raise ImageEmbedError(f"image read-back validation failed: {details}")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _prepare_image(
    identifier: str, spec: object, downscale_width_px: int | None = None
) -> _PreparedImage:
    if not isinstance(spec, ImageSpec):
        raise ImageEmbedError(f"image mapping for {identifier} must be an ImageSpec")
    path = Path(spec.path)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ImageEmbedError(f"cannot read PNG for {identifier}: {path}: {exc}") from exc
    if downscale_width_px is not None:
        # Re-encode before the dimensions are read: the picture fragment and the
        # geometry read-back both describe the bytes that end up in BinData.
        payload = downscale_png(payload, identifier, downscale_width_px)
    pixel_width, pixel_height = png_dimensions(payload, identifier)
    caption = _normalize_caption(spec.caption, identifier)
    if pixel_width * HWP_UNITS_PER_PIXEL_96_DPI > MAX_U32:
        raise ImageEmbedError(f"PNG width is too large for HWPX units: {identifier}")
    if pixel_height * HWP_UNITS_PER_PIXEL_96_DPI > MAX_U32:
        raise ImageEmbedError(f"PNG height is too large for HWPX units: {identifier}")
    return _PreparedImage(
        identifier=identifier,
        payload=payload,
        caption=caption,
        pixel_width=pixel_width,
        pixel_height=pixel_height,
    )


def _normalize_caption(caption: object, identifier: str) -> str:
    if not isinstance(caption, str):
        raise ImageEmbedError(f"caption for {identifier} must be text")
    if any(ord(character) < 32 and character not in "\t\r\n" for character in caption):
        raise ImageEmbedError(f"caption for {identifier} contains an invalid control character")
    normalized = _LEADING_FIGURE_NUMBER_RE.sub("", " ".join(caption.split()))
    if not normalized:
        raise ImageEmbedError(f"caption for {identifier} must not be empty")
    return normalized


def _reject_manifest_collisions(
    content_hpf: bytes,
    entries: Mapping[str, bytes],
    prepared: Mapping[str, _PreparedImage],
) -> None:
    try:
        root = ET.fromstring(content_hpf)
    except ET.ParseError as exc:
        raise ImageEmbedError(f"malformed {CONTENT_HPF_ENTRY}: {exc}") from exc

    manifest = root.find(f".//{{{OPF_NS}}}manifest")
    if manifest is None or b"</opf:manifest>" not in content_hpf:
        raise ImageEmbedError(f"missing opf:manifest in {CONTENT_HPF_ENTRY}")
    item_ids: set[str] = set()
    hrefs: set[str] = set()
    for item in manifest.findall(f"{{{OPF_NS}}}item"):
        item_id = item.get("id", "")
        href = item.get("href", "")
        if item_id in item_ids:
            raise ImageEmbedError(f"duplicate existing opf:item id: {item_id}")
        if href in hrefs:
            raise ImageEmbedError(f"duplicate existing opf:item href: {href}")
        item_ids.add(item_id)
        hrefs.add(href)

    for item in prepared.values():
        if item.identifier in item_ids:
            raise ImageEmbedError(f"opf:item id already exists: {item.identifier}")
        if item.href in hrefs or item.href in entries:
            raise ImageEmbedError(f"BinData target already exists: {item.href}")


def _add_manifest_items(
    content_hpf: bytes,
    prepared: Mapping[str, _PreparedImage],
) -> bytes:
    closing_tag = b"</opf:manifest>"
    insertion = b"".join(
        (
            f'<opf:item id="{item.identifier}" href="{item.href}" '
            f'media-type="{NEW_IMAGE_MEDIA_TYPE}" isEmbeded="1"/>'
        ).encode("ascii")
        for item in prepared.values()
    )
    return content_hpf.replace(closing_tag, insertion + closing_tag, 1)


def _body_width(section_xml: bytes) -> int:
    try:
        root = ET.fromstring(section_xml)
    except ET.ParseError as exc:
        raise ImageEmbedError(f"malformed section XML: {exc}") from exc
    page = root.find(f".//{{{HP_NS}}}pagePr")
    if page is None:
        return DEFAULT_BODY_WIDTH
    margin = page.find(f"{{{HP_NS}}}margin")
    if margin is None:
        return DEFAULT_BODY_WIDTH
    try:
        width = int(page.get("width", "0"))
        left = int(margin.get("left", "0"))
        right = int(margin.get("right", "0"))
    except ValueError as exc:
        raise ImageEmbedError("section page geometry contains a non-integer value") from exc
    body_width = width - left - right
    if body_width <= 0:
        raise ImageEmbedError("section body width must be positive")
    return body_width


def _display_dimensions(
    image: _PreparedImage, body_width: int, max_display_width: int = MAX_DISPLAY_WIDTH
) -> tuple[int, int]:
    original_width = image.pixel_width * HWP_UNITS_PER_PIXEL_96_DPI
    original_height = image.pixel_height * HWP_UNITS_PER_PIXEL_96_DPI
    width_cap = min(body_width, max_display_width)
    if width_cap <= 0:
        raise ImageEmbedError("section body width must be positive")

    if original_width <= width_cap and original_height <= MAX_DISPLAY_HEIGHT:
        return original_width, original_height
    if width_cap * original_height <= MAX_DISPLAY_HEIGHT * original_width:
        width = width_cap
        height = _round_ratio(original_height * width, original_width)
    else:
        height = MAX_DISPLAY_HEIGHT
        width = _round_ratio(original_width * height, original_height)
    width = max(1, min(width, width_cap))
    height = max(1, min(height, MAX_DISPLAY_HEIGHT))
    aspect_error = abs(width * image.pixel_height - height * image.pixel_width) / (
        height * image.pixel_width
    )
    if aspect_error > 0.001:
        raise ImageEmbedError(
            f"image aspect ratio cannot be represented within HWPX caps: {image.identifier}"
        )
    return width, height


def _round_ratio(numerator: int, denominator: int) -> int:
    return (numerator + denominator // 2) // denominator


def _picture_fragment(
    image: _PreparedImage,
    *,
    figure_number: int,
    z_order: int,
    body_width: int,
    max_display_width: int,
    allocator: _StableIdAllocator,
    caption_char_id: str,
    host_para_id: str,
) -> str:
    original_width = image.pixel_width * HWP_UNITS_PER_PIXEL_96_DPI
    original_height = image.pixel_height * HWP_UNITS_PER_PIXEL_96_DPI
    current_width, current_height = _display_dimensions(
        image, body_width, max_display_width
    )
    scale_x = current_width / original_width
    scale_y = current_height / original_height
    paragraph_id = allocator.allocate(f"{image.identifier}:paragraph")
    picture_id = allocator.allocate(f"{image.identifier}:picture")
    instance_id = allocator.allocate(f"{image.identifier}:instance")
    caption_paragraph_id = allocator.allocate(f"{image.identifier}:caption-paragraph")
    caption_text = escape(f"그림 {figure_number}. {image.caption}", quote=False)
    shape_comment = f"{image.identifier}.png {image.pixel_width}x{image.pixel_height}"

    return (
        f'<hp:p id="{paragraph_id}" paraPrIDRef="{host_para_id}" styleIDRef="0" '
        f'pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="0">'
        f'<hp:pic id="{picture_id}" zOrder="{z_order}" numberingType="PICTURE" '
        f'textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" '
        f'dropcapstyle="None" href="" groupLevel="0" instid="{instance_id}" reverse="0">'
        f'<hp:offset x="0" y="0"/>'
        f'<hp:orgSz width="{original_width}" height="{original_height}"/>'
        f'<hp:curSz width="{current_width}" height="{current_height}"/>'
        f'<hp:flip horizontal="0" vertical="0"/>'
        f'<hp:rotationInfo angle="0" centerX="{current_width // 2}" '
        f'centerY="{current_height // 2}" rotateimage="1"/>'
        f'<hp:renderingInfo>'
        f'<hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'<hc:scaMatrix e1="{scale_x:.6f}" e2="0" e3="0" e4="0" '
        f'e5="{scale_y:.6f}" e6="0"/>'
        f'<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
        f'</hp:renderingInfo>'
        f'<hc:img binaryItemIDRef="{image.identifier}" bright="0" contrast="0" '
        f'effect="REAL_PIC" alpha="0"/>'
        f'<hp:imgRect>'
        f'<hc:pt0 x="0" y="0"/><hc:pt1 x="{original_width}" y="0"/>'
        f'<hc:pt2 x="{original_width}" y="{original_height}"/>'
        f'<hc:pt3 x="0" y="{original_height}"/>'
        f'</hp:imgRect>'
        f'<hp:imgClip left="0" right="{original_width}" top="0" '
        f'bottom="{original_height}"/>'
        f'<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
        f'<hp:imgDim dimwidth="{original_width}" dimheight="{original_height}"/>'
        f'<hp:effects/>'
        f'<hp:sz width="{current_width}" widthRelTo="ABSOLUTE" '
        f'height="{current_height}" heightRelTo="ABSOLUTE" protect="0"/>'
        # 글자처럼 취급(인라인): 그림은 자기 문단의 한 줄이라 남은 공간이 모자라면
        # 줄째 다음 쪽으로 가고 본문을 덮을 수 없다. 떠 있는 개체(treatAsChar=0,
        # flowWithText=1)는 뷰어가 쪽 안에 가두려고 위로 끌어올려 앞 본문을 덮었다.
        f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" '
        f'allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" '
        f'horzRelTo="COLUMN" vertAlign="TOP" horzAlign="CENTER" '
        f'vertOffset="0" horzOffset="0"/>'
        f'<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
        f'<hp:shapeComment>{shape_comment}</hp:shapeComment>'
        f'</hp:pic></hp:run></hp:p>'
        # 캡션은 hp:caption 이 아니라 그림 바로 다음의 평범한 문단이다. 줄 배치
        # (linesegarray)를 싣지 않는 문서에서 뷰어는 hp:caption 의 글줄 높이를
        # 예약하지 않아 캡션이 다음 문장 위에 그려졌다. 문단은 스스로 줄을 차지한다.
        f'<hp:p id="{caption_paragraph_id}" paraPrIDRef="{CAPTION_PARA_ID}" '
        f'styleIDRef="{CAPTION_STYLE_ID}" pageBreak="0" columnBreak="0" merged="0">'
        f'<hp:run charPrIDRef="{caption_char_id}"><hp:t>{caption_text}</hp:t></hp:run>'
        f'</hp:p>'
    )


def _image_sort_key(identifier: str) -> int:
    return int(identifier.removeprefix("image"))


__all__ = [
    "MAX_DISPLAY_HEIGHT",
    "MAX_DISPLAY_WIDTH",
    "ImageEmbedError",
    "ImageSpec",
    "embed_images",
    "mm_to_hwp_units",
]
