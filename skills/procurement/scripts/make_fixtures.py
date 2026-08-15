"""Synthetic procurement templates (W4-4) — stdlib-only zip/XML builders.

Real forms come from cha ([USER] handoff). Until then these fixtures prove the
mechanism: legacy placeholder docx/xlsx/hwpx, a DOCX token split across runs, a
labeled HWPX empty-cell form, a refused binary .hwp stub, and optional large
HWPX (>25 MiB via a stored random pad member) for the Drive-link branch.
"""
from __future__ import annotations

import argparse
import os
import zipfile
from pathlib import Path

XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
LINES = ("품목: {{품목}}", "금액: {{금액}}", "업체: {{업체}}", "작성일자: {{일자}}")
NOTE = "본 양식은 합성 테스트 템플릿입니다 (실양식은 [USER] 핸드오프 대기)."


def _docx(out: Path) -> None:
    ct = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type='
        '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
        ' Target="word/document.xml"/></Relationships>'
    )
    paragraphs = "".join(
        '<w:p><w:r><w:t>품목: {{품</w:t></w:r><w:r><w:t>목}}</w:t></w:r></w:p>'
        if text == "품목: {{품목}}" else f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        for text in ("구매 요청서", NOTE, *LINES)
    )
    document = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}<w:sectPr/></w:body></w:document>"
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", XML_HEAD + ct)
        archive.writestr("_rels/.rels", XML_HEAD + rels)
        archive.writestr("word/document.xml", XML_HEAD + document)


def _xlsx(out: Path) -> None:
    ct = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
    )
    root_rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type='
        '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
        ' Target="xl/workbook.xml"/></Relationships>'
    )
    workbook = (
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="지출품의" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    wb_rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type='
        '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
        ' Target="worksheets/sheet1.xml"/></Relationships>'
    )
    rows = "".join(
        f'<row r="{i}"><c r="A{i}" t="inlineStr"><is><t>{text}</t></is></c></row>'
        for i, text in enumerate(("지출 품의서", NOTE, *LINES), start=1)
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{rows}</sheetData></worksheet>"
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", XML_HEAD + ct)
        archive.writestr("_rels/.rels", XML_HEAD + root_rels)
        archive.writestr("xl/workbook.xml", XML_HEAD + workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", XML_HEAD + wb_rels)
        archive.writestr("xl/worksheets/sheet1.xml", XML_HEAD + sheet)


def _hwpx(out: Path, pad_bytes: int = 0) -> None:
    paragraphs = "".join(
        f'<hp:p id="900000000{i}"><hp:run charPrIDRef="0"><hp:t>{text}</hp:t></hp:run></hp:p>'
        for i, text in enumerate(("용역 요청서", NOTE, *LINES), start=1)
    )
    section = (
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        f"{paragraphs}</hs:sec>"
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"), "application/hwp+zip", compress_type=zipfile.ZIP_STORED
        )
        archive.writestr("Contents/section0.xml", XML_HEAD + section)
        archive.writestr("Contents/header.xml", XML_HEAD + '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"/>')
        if pad_bytes > 0:
            archive.writestr(
                zipfile.ZipInfo("BinData/pad.bin"), os.urandom(pad_bytes),
                compress_type=zipfile.ZIP_STORED,
            )


def _hwp_stub(out: Path) -> None:
    _ = out.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504)


def _hwpx_form(out: Path) -> None:
    labels = ("품목", "금액", "업체", "일자")
    rows = "".join("".join((
        '<hp:tr>',
        f'<hp:tc><hp:cellAddr colAddr="0" rowAddr="{row}"/><hp:cellSpan colSpan="1" rowSpan="1"/>',
        f'<hp:subList><hp:p id="91000000{row}1"><hp:run charPrIDRef="0"><hp:t>{label}</hp:t></hp:run></hp:p></hp:subList></hp:tc>',
        f'<hp:tc><hp:cellAddr colAddr="1" rowAddr="{row}"/><hp:cellSpan colSpan="1" rowSpan="1"/>',
        f'<hp:subList><hp:p id="91000000{row}2"><hp:run charPrIDRef="0"><hp:t/></hp:run></hp:p></hp:subList></hp:tc>',
        '</hp:tr>',
    )) for row, label in enumerate(labels))
    section = (
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        f'<hp:p id="9000000001"><hp:run charPrIDRef="0"><hp:tbl>{rows}</hp:tbl></hp:run></hp:p></hs:sec>'
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("Contents/header.xml", XML_HEAD + '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"/>')
        archive.writestr("Contents/section0.xml", XML_HEAD + section)


def main() -> None:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("outdir")
    _ = parser.add_argument("--large-hwpx-bytes", type=int, default=0,
                            help="also emit a padded hwpx of roughly this many extra bytes")
    args = parser.parse_args()
    outdir = Path(str(args.outdir))
    large_hwpx_bytes = int(args.large_hwpx_bytes)
    outdir.mkdir(parents=True, exist_ok=True)
    _docx(outdir / "구매요청서-샘플.docx")
    _xlsx(outdir / "지출품의-샘플.xlsx")
    _hwpx(outdir / "용역요청서-샘플.hwpx")
    _hwpx_form(outdir / "빈슬롯-구매요청서-샘플.hwpx")
    _hwp_stub(outdir / "구양식-샘플.hwp")
    made = ["구매요청서-샘플.docx", "지출품의-샘플.xlsx", "용역요청서-샘플.hwpx", "빈슬롯-구매요청서-샘플.hwpx", "구양식-샘플.hwp"]
    if large_hwpx_bytes > 0:
        _hwpx(outdir / "대형-용역요청서-샘플.hwpx", pad_bytes=large_hwpx_bytes)
        made.append("대형-용역요청서-샘플.hwpx")
    print(f"FIXTURES-MADE dir={outdir} files={','.join(made)}")


if __name__ == "__main__":
    main()
