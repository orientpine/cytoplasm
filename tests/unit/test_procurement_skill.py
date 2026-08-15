"""W4-4 procurement skill — pure-logic and stdlib-path tests (no docx/openpyxl)."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from skills.procurement.scripts import make_fixtures
from skills.procurement.scripts import procure_core as core
from skills.procurement.scripts import procure_generate as gen


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory) -> Path:
    outdir = tmp_path_factory.mktemp("fx")
    make_fixtures._docx(outdir / "t.docx")
    make_fixtures._xlsx(outdir / "t.xlsx")
    make_fixtures._hwpx(outdir / "t.hwpx")
    make_fixtures._hwp_stub(outdir / "t.hwp")
    return outdir


# ------------------------------------------------------------------ preflight
@pytest.mark.parametrize(
    ("name", "fmt", "parser"),
    [
        ("t.docx", "docx", "python-docx"),
        ("t.xlsx", "xlsx", "openpyxl"),
        ("t.hwpx", "hwpx", "zip+XML(stdlib)"),
    ],
)
def test_detect_supported(fixtures: Path, name: str, fmt: str, parser: str) -> None:
    result = core.detect_format(name, (fixtures / name).read_bytes())
    assert (result.format, result.parser) == (fmt, parser)


def test_detect_binary_hwp_refused(fixtures: Path) -> None:
    with pytest.raises(core.UnsupportedTemplate) as error:
        core.detect_format("t.hwp", (fixtures / "t.hwp").read_bytes())
    assert "CONVERSION-REQUEST" in error.value.conversion_request
    assert "생성을 시도하지 않았습니다" in error.value.conversion_request


def test_detect_hwp_by_extension_even_if_zip(fixtures: Path) -> None:
    data = (fixtures / "t.hwpx").read_bytes()
    with pytest.raises(core.UnsupportedTemplate):
        core.detect_format("renamed.hwp", data)


def test_detect_non_zip_refused() -> None:
    with pytest.raises(core.UnsupportedTemplate):
        core.detect_format("junk.docx", b"not a zip at all")


def test_detect_zip_without_body_refused(tmp_path: Path) -> None:
    target = tmp_path / "empty.docx"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("nothing/here.txt", "x")
    with pytest.raises(core.UnsupportedTemplate):
        core.detect_format("empty.docx", target.read_bytes())


def test_extract_placeholders_order_dedup(fixtures: Path) -> None:
    data = (fixtures / "t.hwpx").read_bytes()
    result = core.detect_format("t.hwpx", data)
    assert core.extract_placeholders(result, data) == ("품목", "금액", "업체", "일자")


# ------------------------------------------------------------------ fields
def test_normalize_amount_happy() -> None:
    assert core.normalize_amount("1,234,000원") == "1,234,000원"
    assert core.normalize_amount("55000") == "55,000원"
    assert core.normalize_amount(" 990 KRW ") == "990원"


@pytest.mark.parametrize("raw", ["많이요", "", "0원", "-500", "약 3만원"])
def test_normalize_amount_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        core.normalize_amount(raw)


def test_missing_fields_auto_date() -> None:
    placeholders = ("품목", "금액", "업체", "일자")
    assert core.missing_fields(placeholders, {}) == ("품목", "금액", "업체")
    assert core.missing_fields(placeholders, {"품목": "a", "금액": "1원", "업체": "b"}) == ()


def test_render_refusal_lists_missing() -> None:
    text = core.render_refusal(("금액", "업체"))
    assert "GENERATION-REFUSED" in text
    assert "- 금액" in text and "- 업체" in text


# ------------------------------------------------------------------ size branch
def test_review_mode_boundary() -> None:
    assert core.review_mode(core.DM_MAX_BYTES) == "attach"
    assert core.review_mode(core.DM_MAX_BYTES + 1) == "drive-link"
    assert core.review_mode(1, max_bytes=1024) == "attach"
    assert core.review_mode(1025, max_bytes=1024) == "drive-link"


# ------------------------------------------------------------------ hwpx roundtrip
def test_hwpx_generate_and_verify(fixtures: Path, tmp_path: Path) -> None:
    template = fixtures / "t.hwpx"
    result = core.detect_format("t.hwpx", template.read_bytes())
    fields = {"품목": "시약 <A&B>", "금액": "77,000원", "업체": "합성벤더", "일자": "2026-07-16"}
    out = tmp_path / "draft.hwpx"
    gen.generate(result, template, fields, out)
    assert gen.verify(result, out, fields) == sorted(fields)


def test_hwpx_verify_fails_on_leftover_placeholder(fixtures: Path, tmp_path: Path) -> None:
    template = fixtures / "t.hwpx"
    result = core.detect_format("t.hwpx", template.read_bytes())
    out = tmp_path / "partial.hwpx"
    gen.generate(result, template, {"품목": "x"}, out)  # others left as {{…}}
    with pytest.raises(gen.VerifyFailed):
        gen.verify(result, out, {"품목": "x", "금액": "1원"})


def test_docx_generate_uses_stdlib_xml_without_python_docx(fixtures: Path, tmp_path: Path) -> None:
    template = fixtures / "t.docx"
    result = core.detect_format("t.docx", template.read_bytes())
    output = tmp_path / "xml-first.docx"
    fields = {"품목": "x", "금액": "1원", "업체": "y", "일자": "2026-07-17"}
    gen.generate(result, template, fields, output)
    assert gen.verify(result, output, fields) == sorted(fields)
