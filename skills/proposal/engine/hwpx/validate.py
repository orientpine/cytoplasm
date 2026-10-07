from __future__ import annotations

import argparse
import json
import logging
import os
import re
import zipfile
from collections import Counter, OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast
from xml.etree import ElementTree

from ..contracts import CitationStatus, KPI
from ..contracts.layout_profile import (
    LEGACY_LAYOUT_PROFILE_NAME,
    LayoutProfile,
    get_layout_profile,
)
from ..contracts.validators import (
    PMSQuery,
    SectionDraft,
    validate_citation_cover,
    validate_kpi_sum,
    validate_sections,
)
from ._image_contract import (
    DEFAULT_BODY_WIDTH,
    HWP_UNITS_PER_PIXEL_96_DPI,
    MAX_DISPLAY_HEIGHT,
    MAX_DISPLAY_WIDTH,
    ImageEmbedError,
    png_dimensions,
)
from .seed_fill import form_section_headings
from .zip_surgery import (
    NEW_ENTRY_CREATE_SYSTEM,
    NEW_ENTRY_EXTERNAL_ATTR,
    repack,
    replace_entry,
    unpack,
)

HWPX_MIMETYPE = b"application/hwp+zip"
MIMETYPE_ENTRY = "mimetype"
SECTION0_ENTRY = "Contents/section0.xml"
CONTENT_HPF_ENTRY = "Contents/content.hpf"
PREVIEW_ENTRY = "Preview/PrvText.txt"
HP_NS = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HC_NS = "http://www.hancom.co.kr/hwpml/2011/core"
OPF_NS = "http://www.idpf.org/2007/opf/"
# Backward-compatible alias; active validation uses the selected profile's cap.
MAX_PUBLIC_ARTIFACT_CHARS = get_layout_profile(
    LEGACY_LAYOUT_PROFILE_NAME
).max_public_artifact_chars

CitationCover = Literal["PASS", "FAIL", "UNCHECKED"]

# Which seed 대제목 stands for each required section token, and the prose spellings
# a draft may use instead of the form's own outline.
SEED_HEADING_SECTION_INDEX: dict[str, int] = {
    "배경·필요성": 1,
    "연구목표": 2,
    "연구내용·방법": 3,
}
_PROSE_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "배경·필요성": ("배경 및 필요성",),
    "연구목표": ("2. 목표", "연구개발 목표", "최종 목표"),
    "연구내용·방법": ("3. 내용·수행방법", "전체 연구내용", "연구 내용 및 방법"),
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    errors: list[str]
    citation_cover: CitationCover


class RepairError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason: str = reason


@dataclass(frozen=True)
class _SidecarClaim:
    text: str
    source_ids: list[str]


@dataclass(frozen=True)
class _SidecarSection:
    section_id: str
    title: str
    body: str
    claims: list[_SidecarClaim]


@dataclass(frozen=True)
class _SidecarProvenance:
    source_id: str


@dataclass(frozen=True)
class _SidecarEvidence:
    source_id: str
    status: str

    @property
    def provenances(self) -> Sequence[_SidecarProvenance]:
        return [_SidecarProvenance(self.source_id)]


class _SidecarPMS:
    def __init__(self, evidence: Iterable[_SidecarEvidence]) -> None:
        self._evidence: dict[str, _SidecarEvidence] = {item.source_id: item for item in evidence}

    def public_evidence(self) -> Sequence[_SidecarEvidence]:
        return [item for item in self._evidence.values() if item.status == CitationStatus.PUBLIC.value]

    def resolve(self, source_id: str) -> _SidecarEvidence | None:
        return self._evidence.get(source_id)


def validate_linesegarray(path: str | Path) -> list[str]:
    """Refuse an artifact whose stale line layout cache survived any write path."""
    from .zip_surgery import LINE_LAYOUT_CACHE_RE, is_section_entry

    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as hwpx:
            for name in sorted(hwpx.namelist()):
                if not is_section_entry(name):
                    continue
                surviving = len(LINE_LAYOUT_CACHE_RE.findall(hwpx.read(name)))
                if surviving:
                    errors.append(
                        f"stale line layout cache survived in {name}: "
                        f"{surviving} hp:linesegarray element(s)"
                    )
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append(f"line layout cache read-back failed: {exc}")
    return errors


def validate(
    path: str,
    *,
    citations: str | None = None,
    strict: bool = True,
    profile: LayoutProfile | str | None = None,
) -> ValidationReport:
    active_profile = _resolve_validation_profile(profile)
    errors: list[str] = []
    citation_cover: CitationCover = "UNCHECKED"

    _validate_mimetype(path, errors)
    if errors:
        return ValidationReport(ok=False, errors=errors, citation_cover=citation_cover)

    _validate_zip_readable(path, errors)
    if errors:
        return ValidationReport(ok=False, errors=errors, citation_cover=citation_cover)

    errors.extend(validate_linesegarray(path))

    # One strict read-back gate composes all renderer invariants: image linkage
    # and geometry, table occupancy, figure-density bands, and caption sequence.
    errors.extend(validate_image_links(path))
    errors.extend(_validate_table_occupancy_readback(path))
    try:
        with zipfile.ZipFile(path) as hwpx:
            section_xml = hwpx.read(SECTION0_ENTRY)
        from .figure_density import validate_density_xml

        errors.extend(validate_density_xml(section_xml))
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        errors.append(f"figure density read-back failed: {exc}")

    text = ""
    try:
        text = text_extract(path)
    except (KeyError, ElementTree.ParseError, OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        errors.append(f"section0.xml text extraction failed: {exc}")
        return ValidationReport(ok=False, errors=errors, citation_cover=citation_cover)

    missing_sections = missing_required_sections(text)
    if missing_sections:
        message = f"missing required sections: {', '.join(missing_sections)}"
        if strict:
            errors.append(message)
        else:
            logger.warning(message)

    try:
        validate_kpi_sum_from_text(text)
    except ValueError as exc:
        if strict:
            errors.append(str(exc))
        else:
            logger.warning("KPI validation failed: %s", exc)

    if citations is not None:
        citation_cover = _validate_citation_sidecar(citations, errors)

    _page_guard(text, active_profile)

    return ValidationReport(ok=not errors, errors=errors, citation_cover=citation_cover)


def validate_public_artifact(
    path: str,
    citations: str,
    *,
    profile: LayoutProfile | str | None = None,
) -> ValidationReport:
    report = validate(path, citations=citations, strict=True, profile=profile)
    if report.citation_cover != "PASS":
        errors = [*report.errors]
        if not any("citation cover" in error for error in errors):
            errors.append("citation cover must be 100% PUBLIC")
        return ValidationReport(ok=False, errors=errors, citation_cover=report.citation_cover)
    return report


def auto_repair(path: str, out: str | None = None) -> str | None:
    output_path = out or _default_repair_path(path)

    try:
        with zipfile.ZipFile(path) as hwpx:
            infos = hwpx.infolist()
            if not infos:
                raise RepairError("empty zip archive")
            mimetype_info = next((info for info in infos if info.filename == MIMETYPE_ENTRY), None)
            if mimetype_info is None:
                raise RepairError("unrepairable: missing mimetype entry")
            mimetype_payload = hwpx.read(MIMETYPE_ENTRY)
    except zipfile.BadZipFile as exc:
        raise RepairError(f"unrepairable: bad CRC/truncated zip ({exc})") from exc
    except OSError as exc:
        raise RepairError(f"unrepairable: cannot read zip ({exc})") from exc

    if mimetype_payload != HWPX_MIMETYPE:
        raise RepairError("unrepairable: mimetype content is not application/hwp+zip")

    needs_repair = infos[0].filename != MIMETYPE_ENTRY or mimetype_info.compress_type != zipfile.ZIP_STORED
    if not needs_repair:
        raise RepairError("unrepairable: no supported mimetype ordering/compression corruption found")

    entries = unpack(path)
    if mimetype_info.compress_type != zipfile.ZIP_STORED:
        entries = replace_entry(entries, MIMETYPE_ENTRY, HWPX_MIMETYPE)
    if infos[0].filename != MIMETYPE_ENTRY:
        entries = OrderedDict(
            [(MIMETYPE_ENTRY, entries[MIMETYPE_ENTRY])]
            + [(name, payload) for name, payload in entries.items() if name != MIMETYPE_ENTRY]
        )

    repack(entries, output_path)
    return output_path


def text_extract(path: str) -> str:
    with zipfile.ZipFile(path) as hwpx:
        section_xml = hwpx.read(SECTION0_ENTRY)

    root = ElementTree.fromstring(section_xml)
    texts = [node.text or "" for node in root.findall(".//{*}t")]
    return "\n".join(text for text in texts if text)


def validate_kpi_sum_from_text(text: str) -> None:
    """Text-extraction bridge: parse KPI weights from HWPX body text, then delegate
    to the CANONICAL ``validate_kpi_sum`` from ``contracts.validators``.
    This function contains NO duplicate validation logic — it only parses text and
    forwards to the single authoritative validator.
    """
    weights = _extract_kpi_weights(text)
    if not weights:
        logger.warning("KPI weights could not be parsed from HWPX text; skipping KPI sum gate")
        return
    validate_kpi_sum([_kpi_weight(weight) for weight in weights])


def _kpi_weight(weight: int) -> KPI:
    return KPI(
        name=f"weight-{weight}",
        unit="%",
        baseline="0",
        target="1",
        weight=weight,
        method="parsed from HWPX text",
        env="validation",
        rationale="best-effort HWPX KPI sum gate",
    )


def _validate_mimetype(path: str, errors: list[str]) -> None:
    try:
        with zipfile.ZipFile(path) as hwpx:
            infos = hwpx.infolist()
            if not infos:
                errors.append("mimetype entry missing: empty zip")
                return
            first = infos[0]
            if first.filename != MIMETYPE_ENTRY:
                errors.append("mimetype entry must be first")
            mimetype_info = next((info for info in infos if info.filename == MIMETYPE_ENTRY), None)
            if mimetype_info is None:
                errors.append("mimetype entry missing")
                return
            if mimetype_info.compress_type != zipfile.ZIP_STORED:
                errors.append("mimetype entry must be STORED")
            if mimetype_info.file_size != len(HWPX_MIMETYPE):
                errors.append("mimetype entry must be 19 bytes")
            try:
                content = hwpx.read(MIMETYPE_ENTRY)
            except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
                errors.append(f"mimetype read failed: bad CRC/truncated zip ({exc})")
                return
            if content != HWPX_MIMETYPE:
                errors.append("mimetype content must be application/hwp+zip")
    except zipfile.BadZipFile as exc:
        errors.append(f"bad CRC/truncated zip: {exc}")
    except OSError as exc:
        errors.append(f"cannot open zip: {exc}")


def _validate_zip_readable(path: str, errors: list[str]) -> None:
    try:
        with zipfile.ZipFile(path) as hwpx:
            bad_member = hwpx.testzip()
    except zipfile.BadZipFile as exc:
        errors.append(f"bad CRC/truncated zip: {exc}")
        return
    except OSError as exc:
        errors.append(f"cannot open zip: {exc}")
        return
    if bad_member is not None:
        errors.append(f"bad CRC/truncated zip member: {bad_member}")


@dataclass(frozen=True)
class _PictureReadback:
    picture: ElementTree.Element
    reference: str
    section_name: str
    body_width: int


def validate_image_links(path: str | Path) -> list[str]:
    """Read back image links, picture anchors, dimensions, and captions."""

    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as hwpx:
            names = hwpx.namelist()
            if CONTENT_HPF_ENTRY not in names:
                return [f"image linkage: missing {CONTENT_HPF_ENTRY}"]

            try:
                manifest_root = ElementTree.fromstring(hwpx.read(CONTENT_HPF_ENTRY))
            except (ElementTree.ParseError, UnicodeDecodeError) as exc:
                return [f"image linkage: malformed {CONTENT_HPF_ENTRY}: {exc}"]

            items = manifest_root.findall(f".//{{{OPF_NS}}}item")
            image_items = [
                item
                for item in items
                if item.get("href", "").startswith("BinData/")
                or item.get("media-type", "").startswith("image/")
            ]
            items_by_id: dict[str, list[ElementTree.Element]] = {}
            for item in items:
                items_by_id.setdefault(item.get("id", ""), []).append(item)

            for item_id, count in Counter(item.get("id", "") for item in items).items():
                if item_id and count > 1:
                    errors.append(f'duplicate opf:item id="{item_id}" ({count} entries)')
            for href, count in Counter(item.get("href", "") for item in image_items).items():
                if href and count > 1:
                    errors.append(f'duplicate image opf:item href="{href}" ({count} entries)')

            bin_members = [
                name
                for name in names
                if name.startswith("BinData/") and not name.endswith("/")
            ]
            member_counts = Counter(bin_members)
            for member, count in member_counts.items():
                if count > 1:
                    errors.append(f'duplicate BinData ZIP member "{member}" ({count} entries)')

            pictures, caption_texts = _read_pictures(hwpx, names, errors)
            references = [picture.reference for picture in pictures]
            reference_counts = Counter(references)

            for reference in references:
                matching_items = items_by_id.get(reference, [])
                if len(matching_items) != 1:
                    errors.append(
                        f'dangling binaryItemIDRef="{reference}": expected exactly one '
                        f"content.hpf opf:item, found {len(matching_items)}"
                    )
                    continue
                item = matching_items[0]
                href = item.get("href", "")
                expected_href = f"BinData/{reference}.png"
                if href != expected_href:
                    errors.append(
                        f'binaryItemIDRef="{reference}" must link to {expected_href}, got {href}'
                    )
                if member_counts[href] != 1:
                    errors.append(
                        f'dangling binaryItemIDRef="{reference}": expected exactly one ZIP '
                        f'member "{href}", found {member_counts[href]}'
                    )

            for item in image_items:
                item_id = item.get("id", "")
                href = item.get("href", "")
                if reference_counts[item_id] == 0:
                    errors.append(f'orphan image opf:item id="{item_id}" href="{href}"')
                if member_counts[href] == 0:
                    errors.append(f'dangling image opf:item id="{item_id}": ZIP member "{href}" missing')
                if item.get("media-type") != "image/png":
                    errors.append(f'image opf:item id="{item_id}" must use media-type="image/png"')
                if item.get("isEmbeded") != "1":
                    errors.append(f'image opf:item id="{item_id}" must set isEmbeded="1"')

            for member, count in member_counts.items():
                matching_items = [item for item in image_items if item.get("href") == member]
                if not matching_items:
                    errors.append(f'orphan BinData ZIP member "{member}"')
                elif len(matching_items) != 1:
                    errors.append(
                        f'BinData ZIP member "{member}" must have exactly one image opf:item, '
                        f"found {len(matching_items)}"
                    )
                if count == 1:
                    info = hwpx.getinfo(member)
                    if info.compress_type != zipfile.ZIP_STORED:
                        errors.append(f'BinData ZIP member "{member}" must be STORED')
                    if info.external_attr != NEW_ENTRY_EXTERNAL_ATTR:
                        errors.append(
                            f'BinData ZIP member "{member}" has invalid external_attr '
                            f"{info.external_attr}"
                        )
                    if info.create_system != NEW_ENTRY_CREATE_SYSTEM:
                        errors.append(
                            f'BinData ZIP member "{member}" has invalid create_system '
                            f"{info.create_system}"
                        )

            spine_ids = {
                item.get("idref", "")
                for item in manifest_root.findall(f".//{{{OPF_NS}}}spine/{{{OPF_NS}}}itemref")
            }
            for image_id in sorted(spine_ids.intersection(items_by_id)):
                if any(item in image_items for item in items_by_id[image_id]):
                    errors.append(f'image opf:item id="{image_id}" must not appear in opf:spine')

            _validate_picture_ids(pictures, errors)
            _validate_picture_geometry(hwpx, pictures, items_by_id, member_counts, errors)
            _validate_caption_sequence(pictures, caption_texts, hwpx, names, errors)
    except zipfile.BadZipFile as exc:
        errors.append(f"image linkage: bad ZIP: {exc}")
    except OSError as exc:
        errors.append(f"image linkage: cannot read HWPX: {exc}")
    return errors


def _read_pictures(
    hwpx: zipfile.ZipFile,
    names: list[str],
    errors: list[str],
) -> tuple[list[_PictureReadback], list[str]]:
    pictures: list[_PictureReadback] = []
    caption_texts: list[str] = []
    section_names = sorted(
        {
            name
            for name in names
            if re.fullmatch(r"Contents/section\d+\.xml", name) is not None
        },
        key=lambda name: int(name.removeprefix("Contents/section").removesuffix(".xml")),
    )
    for section_name in section_names:
        section_bytes = hwpx.read(section_name)
        unresolved = re.findall(rb"<!--IMAGE:(image\d+)-->", section_bytes)
        if unresolved:
            identifiers = ", ".join(item.decode("ascii") for item in unresolved)
            errors.append(f"unresolved image placeholders in {section_name}: {identifiers}")
        try:
            root = ElementTree.fromstring(section_bytes)
        except ElementTree.ParseError as exc:
            errors.append(f"image linkage: malformed {section_name}: {exc}")
            continue

        parent_by_child = {child: parent for parent in root.iter() for child in parent}
        body_width = _read_body_width(root, section_name, errors)
        section_pictures = root.findall(f".//{{{HP_NS}}}pic")
        direct_images: set[ElementTree.Element] = set()
        for picture in section_pictures:
            parent = parent_by_child.get(picture)
            grandparent = parent_by_child.get(parent) if parent is not None else None
            if (
                parent is None
                or grandparent is None
                or parent.tag != f"{{{HP_NS}}}run"
                or grandparent.tag != f"{{{HP_NS}}}p"
            ):
                errors.append(f"hp:pic in {section_name} must be nested inside hp:p > hp:run")

            image_nodes = picture.findall(f"./{{{HC_NS}}}img")
            if len(image_nodes) != 1:
                errors.append(
                    f"hp:pic in {section_name} must contain exactly one hc:img, "
                    f"found {len(image_nodes)}"
                )
                reference = ""
            else:
                image_node = image_nodes[0]
                direct_images.add(image_node)
                reference = image_node.get("binaryItemIDRef", "")
                if not reference:
                    errors.append(f"hc:img in {section_name} has empty binaryItemIDRef")

            position = picture.find(f"./{{{HP_NS}}}pos")
            # 글자처럼 취급: 떠 있는 그림(treatAsChar=0, flowWithText=1)은 앵커 아래
            # 공간이 모자라면 뷰어가 쪽 안으로 끌어올려 앞 본문을 덮었다(2026-09-30).
            if position is None or position.get("treatAsChar") not in {"1", "true"}:
                errors.append(f"hp:pic in {section_name} must be inline with treatAsChar=1")
            elif position.get("horzAlign") != "CENTER":
                errors.append(f"hp:pic in {section_name} must centre itself with horzAlign=CENTER")

            # 캡션은 그림 문단 바로 다음 문단이다. hp:caption 은 줄 배치가 없는
            # 문서에서 글줄 높이를 예약받지 못해 다음 문장 위에 그려졌다.
            if picture.findall(f"./{{{HP_NS}}}caption"):
                errors.append(
                    f"hp:pic in {section_name} must not carry hp:caption; "
                    "the caption is the paragraph after the picture"
                )
            caption_texts.append(_following_paragraph_text(grandparent, parent_by_child))

            pictures.append(
                _PictureReadback(
                    picture=picture,
                    reference=reference,
                    section_name=section_name,
                    body_width=body_width,
                )
            )

        for image_node in root.findall(f".//{{{HC_NS}}}img"):
            if image_node not in direct_images:
                errors.append(f"hc:img in {section_name} must be a direct child of hp:pic")
    return pictures, caption_texts


def _following_paragraph_text(
    paragraph: ElementTree.Element | None,
    parent_by_child: dict[ElementTree.Element, ElementTree.Element],
) -> str:
    container = parent_by_child.get(paragraph) if paragraph is not None else None
    if container is None or paragraph is None:
        return ""
    siblings = list(container)
    position = siblings.index(paragraph)
    if position + 1 >= len(siblings) or siblings[position + 1].tag != f"{{{HP_NS}}}p":
        return ""
    return "".join(node.text or "" for node in siblings[position + 1].iter(f"{{{HP_NS}}}t"))


def _read_body_width(
    root: ElementTree.Element,
    section_name: str,
    errors: list[str],
) -> int:
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
    except ValueError:
        errors.append(f"invalid page geometry in {section_name}")
        return DEFAULT_BODY_WIDTH
    body_width = width - left - right
    if body_width <= 0:
        errors.append(f"non-positive body width in {section_name}")
        return DEFAULT_BODY_WIDTH
    return body_width


def _validate_picture_ids(pictures: list[_PictureReadback], errors: list[str]) -> None:
    picture_ids = [item.picture.get("id", "") for item in pictures]
    instance_ids = [item.picture.get("instid", "") for item in pictures]
    for label, values in (("hp:pic id", picture_ids), ("hp:pic instid", instance_ids)):
        if any(not value.isdigit() or int(value) > 2**32 - 1 for value in values):
            errors.append(f"{label} values must be unsigned 32-bit integers")
        for value, count in Counter(values).items():
            if value and count > 1:
                errors.append(f'duplicate {label}="{value}" ({count} pictures)')


def _validate_picture_geometry(
    hwpx: zipfile.ZipFile,
    pictures: list[_PictureReadback],
    items_by_id: dict[str, list[ElementTree.Element]],
    member_counts: Counter[str],
    errors: list[str],
) -> None:
    dimensions_by_href: dict[str, tuple[int, int]] = {}
    for item in pictures:
        matching_items = items_by_id.get(item.reference, [])
        if len(matching_items) != 1:
            continue
        href = matching_items[0].get("href", "")
        if member_counts[href] != 1:
            continue
        if href not in dimensions_by_href:
            try:
                dimensions_by_href[href] = png_dimensions(hwpx.read(href), item.reference)
            except ImageEmbedError as exc:
                errors.append(f'BinData ZIP member "{href}" is not a valid PNG: {exc}')
                continue
        pixel_width, pixel_height = dimensions_by_href[href]
        picture = item.picture
        current = picture.find(f"./{{{HP_NS}}}curSz")
        original = picture.find(f"./{{{HP_NS}}}orgSz")
        image_dimension = picture.find(f"./{{{HP_NS}}}imgDim")
        shape_size = picture.find(f"./{{{HP_NS}}}sz")
        scale = picture.find(f"./{{{HP_NS}}}renderingInfo/{{{HC_NS}}}scaMatrix")
        if any(node is None for node in (current, original, image_dimension, shape_size, scale)):
            errors.append(f"hp:pic for {item.reference} is missing required geometry elements")
            continue
        assert current is not None
        assert original is not None
        assert image_dimension is not None
        assert shape_size is not None
        assert scale is not None

        current_width = _integer_attribute(current, "width", item.reference, errors)
        current_height = _integer_attribute(current, "height", item.reference, errors)
        original_width = _integer_attribute(original, "width", item.reference, errors)
        original_height = _integer_attribute(original, "height", item.reference, errors)
        if None in (current_width, current_height, original_width, original_height):
            continue
        assert current_width is not None
        assert current_height is not None
        assert original_width is not None
        assert original_height is not None
        if current_width <= 0 or current_height <= 0:
            errors.append(f"hp:pic for {item.reference} must have positive display dimensions")
        if current_width > min(item.body_width, MAX_DISPLAY_WIDTH):
            errors.append(f"hp:pic for {item.reference} exceeds the display/body width cap")
        if current_height > MAX_DISPLAY_HEIGHT:
            errors.append(f"hp:pic for {item.reference} exceeds the display height cap")

        expected_width = pixel_width * HWP_UNITS_PER_PIXEL_96_DPI
        expected_height = pixel_height * HWP_UNITS_PER_PIXEL_96_DPI
        if (original_width, original_height) != (expected_width, expected_height):
            errors.append(f"hp:orgSz for {item.reference} must equal source pixels x 75")
        if image_dimension.get("dimwidth") != str(expected_width) or image_dimension.get(
            "dimheight"
        ) != str(expected_height):
            errors.append(f"hp:imgDim for {item.reference} must equal source pixels x 75")
        if shape_size.get("width") != str(current_width) or shape_size.get("height") != str(
            current_height
        ):
            errors.append(f"hp:sz for {item.reference} must equal hp:curSz")
        if current_height > 0:
            aspect_error = abs(
                current_width * pixel_height - current_height * pixel_width
            ) / (current_height * pixel_width)
            if aspect_error > 0.001:
                errors.append(
                    f"hp:pic for {item.reference} exceeds 0.1% source aspect-ratio error"
                )
        _validate_scale_matrix(
            scale,
            item.reference,
            current_width,
            current_height,
            original_width,
            original_height,
            errors,
        )


def _integer_attribute(
    element: ElementTree.Element,
    attribute: str,
    reference: str,
    errors: list[str],
) -> int | None:
    try:
        return int(element.get(attribute, ""))
    except ValueError:
        errors.append(f"hp:pic for {reference} has non-integer {attribute}")
        return None


def _validate_scale_matrix(
    scale: ElementTree.Element,
    reference: str,
    current_width: int,
    current_height: int,
    original_width: int,
    original_height: int,
    errors: list[str],
) -> None:
    for attribute, expected in (
        ("e1", current_width / original_width),
        ("e5", current_height / original_height),
    ):
        value = scale.get(attribute, "")
        if re.fullmatch(r"\d+\.\d{6}", value) is None:
            errors.append(
                f"hc:scaMatrix {attribute} for {reference} must use fixed-point 6 decimals"
            )
            continue
        if abs(float(value) - expected) > 0.000001:
            errors.append(f"hc:scaMatrix {attribute} for {reference} does not match display scale")


def _validate_caption_sequence(
    pictures: list[_PictureReadback],
    caption_texts: list[str],
    hwpx: zipfile.ZipFile,
    names: list[str],
    errors: list[str],
) -> None:
    if not pictures:
        return
    numbers: list[int] = []
    for text in caption_texts:
        match = re.fullmatch(r"그림 (\d+)\.\s+.+", text)
        if match is None:
            errors.append(f'hp:caption text must match "그림 N. ...", got {text!r}')
        else:
            numbers.append(int(match.group(1)))
    expected = list(range(1, len(pictures) + 1))
    if numbers != expected:
        errors.append(f"figure caption numbers must be contiguous 1..N: {numbers} != {expected}")

    if PREVIEW_ENTRY not in names:
        errors.append(f"captions cannot be read back: missing {PREVIEW_ENTRY}")
        return
    try:
        preview = hwpx.read(PREVIEW_ENTRY).decode("utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"captions cannot be read back from {PREVIEW_ENTRY}: {exc}")
        return
    for text in caption_texts:
        if text and text not in preview:
            errors.append(f"caption missing from {PREVIEW_ENTRY}: {text}")


def _validate_table_occupancy_readback(path: str | Path) -> list[str]:
    from .table_writer import validate_table_occupancy

    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as hwpx:
            section_names = sorted(
                name
                for name in hwpx.namelist()
                if re.fullmatch(r"Contents/section\d+\.xml", name) is not None
            )
            for section_name in section_names:
                try:
                    root = ElementTree.fromstring(hwpx.read(section_name))
                except ElementTree.ParseError as exc:
                    errors.append(f"table occupancy read-back failed in {section_name}: {exc}")
                    continue
                for index, table in enumerate(root.findall(f".//{{{HP_NS}}}tbl")):
                    try:
                        validate_table_occupancy(table)
                    except ValueError as exc:
                        errors.append(
                            f"table occupancy read-back failed in {section_name} table {index}: {exc}"
                        )
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append(f"table occupancy read-back failed: {exc}")
    return errors


def _extract_kpi_weights(text: str) -> list[int]:
    explicit_sum = re.search(r"가중치\s*합\s*(\d{1,3})\s*%", text)
    if explicit_sum is not None:
        return [int(explicit_sum.group(1))]

    kpi_window_match = re.search(r"(?:KPI|성과지표|핵심 성과지표)(.*?)(?:추진|일정|예산|$)", text, re.DOTALL)
    if kpi_window_match is None:
        return []
    window = cast(str, kpi_window_match.group(1))
    matches = cast(list[str], re.findall(r"\((\d{1,3})\s*%\)", window))
    return [int(match) for match in matches]


def missing_required_sections(
    text: str, *, seed_path: str | Path | None = None
) -> list[str]:
    """Required sections absent from an artifact's text, seed headings accepted."""
    return validate_sections(_canonical_section_text(text, seed_path=seed_path))


def _canonical_section_text(text: str, *, seed_path: str | Path | None = None) -> str:
    # The required tokens are the draft titles' shorthand. A body that follows the
    # seed form's own outline prints the form's headings instead and never contains
    # them verbatim, so each shorthand also accepts the heading the form prints.
    # That heading is read from the seed rather than held as a literal: the form
    # owns its wording, and a reworded 대제목 must not read as a missing section.
    headings = form_section_headings(seed_path)
    additions = [
        canonical
        for canonical, section_index in SEED_HEADING_SECTION_INDEX.items()
        if canonical not in text
        and (
            headings[section_index] in text
            or any(candidate in text for candidate in _PROSE_SECTION_ALIASES[canonical])
        )
    ]
    if not additions:
        return text
    return "\n".join([text, *additions])


def _validate_citation_sidecar(citations: str, errors: list[str]) -> CitationCover:
    try:
        drafts, pms = _load_citation_sidecar(citations)
        report = validate_citation_cover(
            cast(Sequence[SectionDraft], drafts),
            cast(PMSQuery, cast(object, pms)),
            allowed=(CitationStatus.PUBLIC,),
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"citation sidecar invalid: {exc}")
        return "FAIL"

    if report.ok and report.coverage >= 1.0:
        return "PASS"
    message = (
        f"citation cover must be 100% PUBLIC (coverage={report.coverage:.0%}, "
        f"orphans={report.orphans}, missing={report.missing_sources})"
    )
    errors.append(message)
    return "FAIL"


def _load_citation_sidecar(citations: str) -> tuple[list[_SidecarSection], _SidecarPMS]:
    data = cast(dict[str, object], json.loads(Path(citations).read_text(encoding="utf-8")))
    raw_claims = data.get("claims")
    if not isinstance(raw_claims, list):
        raise ValueError("citations sidecar must contain a claims list")
    claim_items = cast(list[object], raw_claims)

    claims: list[_SidecarClaim] = []
    evidence: list[_SidecarEvidence] = []
    for index, item in enumerate(claim_items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"claim #{index} must be an object")
        claim_item = cast(dict[str, object], item)
        source_id = _required_str(claim_item, "source_id", index)
        status = _required_str(claim_item, "status", index)
        claims.append(_SidecarClaim(text=f"citation claim {index}", source_ids=[source_id]))
        evidence.append(_SidecarEvidence(source_id=source_id, status=status))

    section = _SidecarSection(section_id="citations", title="citations", body="", claims=claims)
    return [section], _SidecarPMS(evidence)


def _required_str(item: dict[str, object], key: str, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"claim #{index} must contain non-empty {key}")
    return value


def _resolve_validation_profile(profile: LayoutProfile | str | None) -> LayoutProfile:
    if isinstance(profile, LayoutProfile):
        return profile
    selected = profile or os.environ.get("KIMM_DOCBOT_PROFILE", LEGACY_LAYOUT_PROFILE_NAME)
    return get_layout_profile(selected)


def _page_guard(text: str, profile: LayoutProfile) -> None:
    expected_budget = sum(profile.prose_budgets.values())
    if len(text) > profile.max_public_artifact_chars:
        logger.warning(
            "HWPX text is %s chars (budget=%s, profile=%s, cap=%s); "
            + "output may exceed approximately %s pages",
            len(text),
            expected_budget,
            profile.name,
            profile.max_public_artifact_chars,
            sum(profile.section_page_targets.values()),
        )


def _default_repair_path(path: str) -> str:
    source = Path(path)
    return str(source.with_name(f"{source.stem}.repaired{source.suffix}"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate KIMM DocBot HWPX artifacts")
    _ = parser.add_argument("file")
    _ = parser.add_argument("--citations")
    _ = parser.add_argument("--strict", action=argparse.BooleanOptionalAction, default=True)
    _ = parser.add_argument("--pages", type=int, default=None, help="expected pages (tolerance: ±1)")
    _ = parser.add_argument(
        "--measurement",
        choices=("auto", "estimate", "soffice"),
        default="auto",
        help="page measurement tier (default: guarded PDF smoke, then labeled estimate)",
    )
    args = parser.parse_args(argv)

    file_path = cast(str, args.file)
    citations = cast(str | None, args.citations)
    strict = cast(bool, args.strict)
    report = validate(file_path, citations=citations, strict=strict)
    output: dict[str, object] = dict(report.__dict__)
    ok = report.ok
    expected_pages = cast(int | None, args.pages)
    if expected_pages is not None:
        from .page_convergence import (
            AutoPageMeasurer,
            EstimateMeasurer,
            SofficePdfMeasurer,
        )

        if expected_pages < 1:
            parser.error("--pages must be positive")
        seed = Path(__file__).resolve().parents[1] / "resource" / "R&D 연구계획서 양식.hwpx"
        measurement_name = cast(str, args.measurement)
        if measurement_name == "estimate":
            measurer = EstimateMeasurer()
        elif measurement_name == "soffice":
            measurer = SofficePdfMeasurer(seed)
        else:
            measurer = AutoPageMeasurer(SofficePdfMeasurer(seed))
        measurement = measurer.measure(file_path)
        output["page_measurement"] = asdict(measurement)
        output["expected_pages"] = expected_pages
        if measurement.kind == "estimate":
            output["estimated_pages"] = measurement.pages
        else:
            output["actual_pages"] = measurement.pages
        within_tolerance = (
            measurement.status == "OK"
            and measurement.pages is not None
            and abs(measurement.pages - expected_pages) <= 1
        )
        output["pages_within_tolerance"] = within_tolerance
        ok = ok and within_tolerance
    output["ok"] = ok
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
