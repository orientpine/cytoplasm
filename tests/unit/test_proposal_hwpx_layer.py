"""HWPX 레이어 결함 회귀.

외부 저장소 시절 유예된 항목들을 내제화 엔진에서 닫는다. 각 테스트는
`Given / When / Then` 세 블록으로 한 가지 행동만 고정한다.
"""

from __future__ import annotations

import inspect
import os
import re
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path
from typing import Final

import pytest

from skills.proposal.engine.contracts import AnchorMap, NodeRef
from skills.proposal.engine.contracts import layout_profile
from skills.proposal.engine.hwpx import figure_density
from skills.proposal.engine.hwpx import image_embed
from skills.proposal.engine.hwpx import validate as hwpx_validate
from skills.proposal.engine.hwpx import xml_writer
from skills.proposal.engine.hwpx._image_contract import (
    HWP_UNITS_PER_PIXEL_96_DPI,
    png_dimensions,
)
from skills.proposal.engine.hwpx.anchor_map import DEFAULT_SEED_PATH
from skills.proposal.engine.hwpx.band_styles import DEFAULT_BAND_STYLES
from skills.proposal.engine.hwpx.xml_writer import remove_direct_paragraphs
from skills.proposal.engine.hwpx.zip_surgery import repack, replace_entry, unpack

_SECTION0_ENTRY: Final = "Contents/section0.xml"

_SECTION_OPEN: Final = (
    '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
)
_PLAIN_PARAGRAPH: Final = (
    '<hp:p id="1"><hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p>'
)
_CAPTIONED_PARAGRAPH: Final = (
    '<hp:p id="2"><hp:run charPrIDRef="0"><hp:pic><hp:caption><hp:subList>'
    '<hp:p id="3"><hp:run charPrIDRef="0"><hp:t>그림 1. 캡션</hp:t></hp:run></hp:p>'
    "</hp:subList></hp:caption></hp:pic></hp:run></hp:p>"
)


def _section_with_caption() -> bytes:
    body = "".join(
        (
            _PLAIN_PARAGRAPH.format(text="첫 문단"),
            _CAPTIONED_PARAGRAPH,
            _PLAIN_PARAGRAPH.format(text="셋째 문단"),
        )
    )
    return f"{_SECTION_OPEN}{body}</hs:sec>".encode()


def test_remove_direct_paragraphs_refuses_a_target_that_nests_a_paragraph() -> None:
    # Given: 캡션(중첩 hp:p)을 품은 문단이 삭제 대상에 들어 있다.
    section = _section_with_caption()

    # When / Then: 잘린 조각을 내보내지 않고 이름 있는 오류로 거부한다.
    with pytest.raises(ValueError) as raised:
        _ = remove_direct_paragraphs(section, (1,))

    assert "1" in str(raised.value)


def test_remove_direct_paragraphs_keeps_deleting_plain_siblings() -> None:
    # Given: 같은 문서의 평범한 문단만 지정한다.
    section = _section_with_caption()

    # When: 중첩 없는 문단을 지운다.
    changed = remove_direct_paragraphs(section, (0,))

    # Then: 삭제는 그대로 동작하고 캡션 문단은 온전히 남는다.
    assert "첫 문단" not in changed.decode("utf-8")
    assert changed.decode("utf-8").count("<hp:p ") == 3


def _seed_with_mutated_heading(tmp_path: Path, old: str, new: str) -> Path:
    """Copy the form seed with one top-level heading reworded by a single word."""
    target = tmp_path / "mutated-seed.hwpx"
    with zipfile.ZipFile(DEFAULT_SEED_PATH) as source, zipfile.ZipFile(target, "w") as copy:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == _SECTION0_ENTRY:
                mutated = payload.replace(old.encode("utf-8"), new.encode("utf-8"))
                assert mutated != payload, f"seed no longer carries {old!r}"
                payload = mutated
            copy.writestr(info, payload, compress_type=info.compress_type)
    return target


def test_required_section_aliases_follow_the_seed_heading_wording(tmp_path: Path) -> None:
    # Given: 양식이 1번 대제목 문구를 한 단어 바꾼 시드와, 그 문구를 인쇄한 본문.
    mutated_seed = _seed_with_mutated_heading(
        tmp_path, "1. 연구 배경 및 필요성", "1. 연구 배경 및 그 필요성"
    )
    text = "0. 연구 요약문\n1. 연구 배경 및 그 필요성\n2. 연구 목표\n4. 기대효과 및 활용 방안"

    # When: 시드를 기준으로 필수 절을 대조한다.
    missing = hwpx_validate.missing_required_sections(text, seed_path=mutated_seed)

    # Then: 바뀐 제목은 여전히 그 절로 읽히고, 정말 없는 절만 누락으로 남는다.
    assert "배경·필요성" not in missing
    assert "연구내용·방법" in missing


def test_the_cloning_body_renderer_is_no_longer_public() -> None:
    # Given: 본문 삽입은 figure_density.render_body_paragraphs 한 곳으로 모였다.
    assert callable(figure_density.render_body_paragraphs)

    # When / Then: 이웃 문단을 복제해 문자 속성을 물려받던 옛 진입점은 남아 있지 않다.
    assert not hasattr(xml_writer, "render_paragraph_blocks")
    assert "render_paragraph_blocks" not in xml_writer.__all__


def test_body_insertion_does_not_inherit_the_neighbour_character_property() -> None:
    # Given: 앵커 문단이 본문과 다른 문자 속성(99)을 쓰는 문서.
    section = (
        f"{_SECTION_OPEN}"
        '<hp:p id="1"><hp:run charPrIDRef="99"><hp:t>1. 연구 배경 및 필요성</hp:t>'
        "</hp:run></hp:p></hs:sec>"
    ).encode("utf-8")
    anchor_map = AnchorMap(slots={"section.1.heading": NodeRef(element_path="hs:sec/hp:p[0]", index=0)})

    # When: 그 앵커 아래에 본문을 삽입한다.
    changed = figure_density.render_body_paragraphs(
        section, "section.1.heading", "본문 한 줄.", anchor_map
    ).decode("utf-8")

    # Then: 삽입된 문단은 밴드 본문 속성을 쓰고 이웃의 99 를 물려받지 않는다.
    assert f'charPrIDRef="{DEFAULT_BAND_STYLES.body_char}"><hp:t>본문 한 줄.' in changed
    assert changed.count('charPrIDRef="99"') == 1


def _synthetic_png(path: Path, size: int) -> Path:
    """A deterministic 8-bit greyscale PNG with one filter-0 scanline per row."""
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        raw.extend((x * x + y * y * 3) % 256 for x in range(size))

    def _chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return len(payload).to_bytes(4, "big") + kind + payload + crc.to_bytes(4, "big")

    header = size.to_bytes(4, "big") + size.to_bytes(4, "big") + bytes((8, 0, 0, 0, 0))
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )
    target = path / f"figure-{size}.png"
    _ = target.write_bytes(payload)
    return target


def _hwpx_with_one_placeholder(path: Path) -> Path:
    entries = unpack(str(DEFAULT_SEED_PATH))
    section = entries[_SECTION0_ENTRY].replace(
        b"</hs:sec>", b"<!--IMAGE:image1--></hs:sec>"
    )
    target = path / "placeholder.hwpx"
    repack(replace_entry(entries, _SECTION0_ENTRY, section), str(target))
    return target


def _embedded_png(artifact: Path) -> bytes:
    with zipfile.ZipFile(artifact) as archive:
        return archive.read("BinData/image1.png")


def test_downscaling_shrinks_the_embedded_figure_bytes(tmp_path: Path) -> None:
    # Given: 본문 배치 폭보다 훨씬 큰 1024x1024 원본 한 장.
    source = _hwpx_with_one_placeholder(tmp_path)
    figure = _synthetic_png(tmp_path, 1024)
    images = {"image1": image_embed.ImageSpec(path=figure, caption="시험 그림")}
    full = image_embed.embed_images(source, tmp_path / "full.hwpx", images)

    # When: 배치 폭(120mm)에 맞춘 다운스케일을 켜고 같은 문서를 임베드한다.
    scaled = image_embed.embed_images(
        source,
        tmp_path / "scaled.hwpx",
        images,
        downscale_width_mm=layout_profile.FIGURE_PLACEMENT_WIDTH_MM,
    )

    # Then: 임베드된 바이트는 줄고, 배치 폭을 채울 화소는 남는다.
    assert len(_embedded_png(scaled)) < len(_embedded_png(full))
    width, _height = png_dimensions(_embedded_png(scaled), "image1")
    assert width >= image_embed.mm_to_hwp_units(
        layout_profile.FIGURE_PLACEMENT_WIDTH_MM
    ) // HWP_UNITS_PER_PIXEL_96_DPI


def test_figure_downscale_is_off_unless_a_profile_asks_for_it() -> None:
    # Given / When: 기본 프로파일과 그림이 15장인 30-page 프로파일.
    default_profile = layout_profile.get_layout_profile()
    long_profile = layout_profile.get_layout_profile("30-page")

    # Then: 다운스케일은 프로파일이 켤 때만 켜진다.
    assert default_profile.figure_downscale_mm is None
    assert long_profile.figure_downscale_mm == layout_profile.FIGURE_PLACEMENT_WIDTH_MM


_BREAK_PARAGRAPH: Final = 'pageBreak="1"'


def _two_section_bands() -> tuple[figure_density.LayoutBand, ...]:
    """Section 1 carries two bands, section 2 opens with its own first band."""
    return tuple(
        figure_density.LayoutBand(
            band_index=index,
            section_id=section_id,
            figures=(
                figure_density.FigureSpec(
                    figure_id=f"fig-{index}",
                    section_id=section_id,
                    caption=f"시험 그림 {index + 1}",
                    band_index=index,
                ),
            ),
            body=f"그림 {index + 1} 은 시험용이다.",
        )
        for index, section_id in enumerate(("1", "1", "2"))
    )


def _band_contents(rendered: str) -> list[str]:
    return re.findall(
        r'<!--LAYOUT-BAND index="\d+"[^>]*>-->(.*?)<!--/LAYOUT-BAND',
        rendered,
        re.DOTALL,
    )


def test_layout_band_renderer_has_no_page_break_option() -> None:
    # Given / When: 기준선 렌더러의 공개 호출 계약을 읽는다.
    parameters = inspect.signature(figure_density.render_layout_bands).parameters

    # Then: 렌더러가 강제 개행 동작을 새 옵션으로 되살릴 수 없다.
    assert "band_page_breaks" not in parameters


def test_layout_bands_emit_no_forced_page_breaks() -> None:
    # Given: 두 절에 걸친 세 밴드와 본문 앵커.
    section = f'{_SECTION_OPEN}<hp:p id="1"><hp:run charPrIDRef="0"><hp:t>앵커</hp:t></hp:run></hp:p></hs:sec>'.encode("utf-8")
    anchor_map = AnchorMap(
        slots={"section.1.body": NodeRef(element_path="hs:sec/hp:p[0]", index=0)}
    )

    # When: 기준선 진입점으로 밴드를 렌더한다.
    rendered = figure_density.render_layout_bands(
        section,
        "section.1.body",
        _two_section_bands(),
        {"fig-0": 1, "fig-1": 2, "fig-2": 3},
        anchor_map,
    ).decode("utf-8")

    # Then: 첫 밴드뿐 아니라 어떤 밴드도 강제 페이지 나눔을 만들지 않는다.
    contents = _band_contents(rendered)
    assert len(contents) == 3
    assert all(_BREAK_PARAGRAPH not in content for content in contents)


def _band_xml(
    index: int,
    section_id: str,
    number: int,
    *,
    leading_break: bool = False,
    text_before_figure: bool = False,
    treat_as_char: str = "1",
) -> str:
    break_paragraph = (
        '<hp:p id="0" paraPrIDRef="1" styleIDRef="1" pageBreak="1" columnBreak="0" '
        'merged="0"><hp:run charPrIDRef="0"><hp:t></hp:t></hp:run></hp:p>'
        if leading_break
        else ""
    )
    prose = (
        '<hp:p id="0" paraPrIDRef="1" styleIDRef="1" pageBreak="0" columnBreak="0" '
        f'merged="0"><hp:run charPrIDRef="0"><hp:t>그림 {number} 은 시험용이다.</hp:t>'
        "</hp:run></hp:p>"
    )
    picture = (
        '<hp:p id="0"><hp:run charPrIDRef="0">'
        f'<hp:pic><hp:pos treatAsChar="{treat_as_char}" vertOffset="0" horzOffset="0"/>'
        '<hp:curSz width="1000" height="1000"/></hp:pic></hp:run></hp:p>'
        f'<hp:p id="0"><hp:run charPrIDRef="0"><hp:t>그림 {number}. 시험</hp:t></hp:run></hp:p>'
    )
    payload = prose + picture if text_before_figure else picture + prose
    return (
        f'<!--LAYOUT-BAND index="{index}" section="{section_id}" figure="fig-{number}" '
        f'number="{number}">-->{break_paragraph}{payload}'
        f'<!--/LAYOUT-BAND index="{index}"-->'
    )


def test_read_back_names_a_page_break_before_a_sections_first_figure() -> None:
    # Given: 절 2 의 첫 밴드가 개행을 강제해 대제목만 남은 페이지를 만드는 산출물.
    orphaned = "".join(
        (
            _band_xml(0, "1", 1),
            _band_xml(1, "1", 2, leading_break=True),
            _band_xml(2, "2", 3, leading_break=True),
        )
    ).encode("utf-8")

    # When: 산출물을 읽어 되돌린다.
    errors = figure_density.validate_density_xml(orphaned)

    # Then: 절의 첫 밴드 개행만 이름 붙은 위반으로 잡힌다.
    orphan_errors = [error for error in errors if error.startswith("SECTION_HEADING_ORPHAN")]
    assert len(orphan_errors) == 1
    assert "band 2" in orphan_errors[0]


def test_read_back_requires_the_figure_first_without_a_leading_break() -> None:
    # Given: 강제 개행 없이 본문이 그림보다 앞서는 밴드.
    text_first = _band_xml(0, "1", 1, text_before_figure=True).encode("utf-8")

    # When: 산출물을 읽어 되돌린다.
    errors = figure_density.validate_density_xml(text_first)

    # Then: 개행 유무와 무관하게 그림이 밴드의 첫 내용이어야 한다.
    assert any(error.startswith("FIGURE_TOP_QUARTER") for error in errors)


def test_band_read_back_accepts_an_inline_figure_with_its_caption_paragraph() -> None:
    # Given: 인라인 그림 문단 뒤에 캡션 문단, 그 뒤에 본문이 오는 밴드.
    band = _band_xml(0, "1", 1).encode("utf-8")

    # When: 산출물을 읽어 되돌린다.
    errors = figure_density.validate_density_xml(band)

    # Then: 위치·캡션 위반이 없고, 캡션의 「그림 1.」 은 본문 인용으로 세지 않는다.
    assert not [e for e in errors if e.startswith(("FIGURE_POSITION", "CAPTION_"))]


def test_band_read_back_rejects_a_floating_figure() -> None:
    # Given: 쪽 안으로 끌어올려져 앞 본문을 덮을 수 있는 떠 있는 그림.
    band = _band_xml(0, "1", 1, treat_as_char="0").encode("utf-8")

    # When / Then: 판독이 그 모양을 이름 붙여 거부한다.
    errors = figure_density.validate_density_xml(band)
    assert any(error.startswith("FIGURE_POSITION_OUT_OF_BAND") for error in errors)


def _embedded_section(artifact: Path) -> str:
    with zipfile.ZipFile(artifact) as archive:
        return archive.read(_SECTION0_ENTRY).decode("utf-8")


def test_embedded_figure_is_inline_and_its_caption_is_the_next_paragraph(
    tmp_path: Path,
) -> None:
    # Given: 그림 자리 하나가 있는 문서와 그림 한 장.
    source = _hwpx_with_one_placeholder(tmp_path)
    images = {"image1": image_embed.ImageSpec(path=_synthetic_png(tmp_path, 64), caption="시험 그림")}

    # When: 그림을 임베드한다.
    section = _embedded_section(image_embed.embed_images(source, tmp_path / "out.hwpx", images))

    # Then: 그림은 글자처럼 취급되고 캡션 개체가 없으며, 캡션은 바로 다음 문단이다.
    assert 'treatAsChar="1"' in re.search(r"<hp:pos\b[^>]*>", section).group(0)
    assert "<hp:caption" not in section
    assert re.search(
        r"</hp:pic></hp:run></hp:p><hp:p\b[^>]*><hp:run\b[^>]*><hp:t>그림 1\. 시험 그림</hp:t>",
        section,
    )


def test_a_caption_that_already_carries_its_number_is_not_numbered_twice(
    tmp_path: Path,
) -> None:
    # Given: figures.json 이 번호를 붙여 보낸 캡션.
    source = _hwpx_with_one_placeholder(tmp_path)
    images = {
        "image1": image_embed.ImageSpec(
            path=_synthetic_png(tmp_path, 64), caption="그림 7. 시험 그림"
        )
    }

    # When: 그림을 임베드한다.
    section = _embedded_section(image_embed.embed_images(source, tmp_path / "out.hwpx", images))

    # Then: 렌더러가 매긴 번호 하나만 남는다.
    assert "<hp:t>그림 1. 시험 그림</hp:t>" in section
    assert "그림 7" not in section


def test_image_read_back_rejects_a_floating_picture(tmp_path: Path) -> None:
    # Given: 임베드된 그림을 떠 있는 개체로 바꾼 산출물.
    source = _hwpx_with_one_placeholder(tmp_path)
    images = {"image1": image_embed.ImageSpec(path=_synthetic_png(tmp_path, 64), caption="시험 그림")}
    embedded = image_embed.embed_images(source, tmp_path / "out.hwpx", images)
    entries = unpack(str(embedded))
    floating = entries[_SECTION0_ENTRY].replace(b'treatAsChar="1"', b'treatAsChar="0"')
    mutated = tmp_path / "floating.hwpx"
    repack(replace_entry(entries, _SECTION0_ENTRY, floating), str(mutated))

    # When / Then: 이미지 판독이 인라인이 아닌 그림을 거부한다.
    errors = hwpx_validate.validate_image_links(mutated)
    assert any("must be inline with treatAsChar=1" in error for error in errors)


def test_png_inflate_rejects_excess_data_without_allocating_it() -> None:
    # Given: a valid zlib stream much larger than the tiny PNG's declared raster.
    script = """
import resource, struct, zlib
from skills.proposal.engine.hwpx.png_scale import downscale_png, _chunk
from skills.proposal.engine.hwpx._image_contract import PNG_SIGNATURE, ImageEmbedError
encoder = zlib.compressobj()
compressed = b''.join(encoder.compress(b'\\0' * 1048576) for _ in range(128))
compressed += encoder.flush()
header = struct.pack('>IIBBBBB', 2, 2, 8, 2, 0, 0, 0)
payload = PNG_SIGNATURE + _chunk(b'IHDR', header) + _chunk(b'IDAT', compressed) + _chunk(b'IEND', b'')
resource.setrlimit(resource.RLIMIT_AS, (67108864, 67108864))
try:
    downscale_png(payload, 'oversized-raster', 1)
except ImageEmbedError:
    print('PNG-REFUSED')
except MemoryError:
    raise SystemExit('UNBOUNDED-PNG-INFLATE')
else:
    raise SystemExit('OVERSIZED-PNG-ACCEPTED')
"""
    # When: the actual decoder runs in an isolated process with bounded memory.
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=20,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
    )
    # Then: malformed data is a domain refusal, not memory exhaustion.
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PNG-REFUSED"


def test_png_factor_search_is_bounded_for_thin_high_resolution_images() -> None:
    # Given: no common divisor exists except one, despite a very large width.
    script = """
import resource
from skills.proposal.engine.hwpx.png_scale import _box_factor
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_CPU, (1, 2))
assert _box_factor(2000000003, 1, 1) == 1
print('FACTOR-BOUNDED')
"""
    # When: actual factor selection receives a CPU budget, not a timed sleep.
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
    )
    # Then: work is bounded by common divisors rather than iterating over width.
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "FACTOR-BOUNDED"
