"""Render an HWPX into page images for human and agent visual review.

This is deliberately a QA renderer, not a Hancom-compatible submission
renderer. It maps the form geometry and the renderer-owned HWPX attributes that
have caused real regressions: paragraph boundaries, typography, indentation,
tables, page breaks, embedded image sizes, captions, and floating pictures.
"""

from __future__ import annotations

import argparse
import base64
import html
import os
import shutil
import subprocess
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HH = "http://www.hancom.co.kr/hwpml/2011/head"
HC = "http://www.hancom.co.kr/hwpml/2011/core"
NS = {"hc": HC, "hh": HH, "hp": HP}
HWPUNIT_PER_INCH = 7200.0
DEFAULT_FONT = "'NanumGothic','Nanum Gothic',sans-serif"
FONT_MAP = {
    "함초롬바탕": "'NanumMyeongjo','Nanum Myeongjo',serif",
    "함초롬돋움": DEFAULT_FONT,
}


@dataclass(frozen=True, slots=True)
class PreviewResult:
    html_path: Path
    pdf_path: Path
    page_paths: tuple[Path, ...]


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _mm(value: float) -> float:
    return value / HWPUNIT_PER_INCH * 25.4


def _parse_header(header: bytes) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    root = ElementTree.fromstring(header)
    fonts: dict[str, str] = {}
    for face in root.iter(f"{{{HH}}}font"):
        fonts.setdefault(face.get("id", ""), face.get("face", ""))

    char_styles: dict[str, dict[str, str]] = {}
    for char_pr in root.iter(f"{{{HH}}}charPr"):
        spacing_node = char_pr.find("hh:spacing", NS)
        ratio_node = char_pr.find("hh:ratio", NS)
        font_ref = char_pr.find("hh:fontRef", NS)
        spacing = float(spacing_node.get("hangul", "0")) if spacing_node is not None else 0.0
        ratio = float(ratio_node.get("hangul", "100")) if ratio_node is not None else 100.0
        face = fonts.get(font_ref.get("hangul", ""), "") if font_ref is not None else ""
        char_styles[char_pr.get("id", "")] = {
            "color": char_pr.get("textColor", "#000000"),
            "font-family": FONT_MAP.get(face, DEFAULT_FONT),
            "font-size": f"{float(char_pr.get('height', '1000')) / 100.0}pt",
            "font-weight": "700" if char_pr.find("hh:bold", NS) is not None else "400",
            "letter-spacing": f"{spacing / 100.0:.4f}em",
            "transform": "" if ratio == 100.0 else f"scaleX({ratio / 100.0:.3f})",
        }

    para_styles: dict[str, str] = {}
    for para_pr in root.iter(f"{{{HH}}}paraPr"):
        align_node = para_pr.find("hh:align", NS)
        horizontal = align_node.get("horizontal", "JUSTIFY") if align_node is not None else "JUSTIFY"
        line_node = para_pr.find(".//hh:lineSpacing", NS)
        line_height = "1.6"
        if line_node is not None and line_node.get("type") == "PERCENT":
            line_height = f"{float(line_node.get('value', '160')) / 100.0:.2f}"
        break_node = para_pr.find("hh:breakSetting", NS)
        keep = break_node is not None and break_node.get("keepWithNext") == "1"
        left_node = para_pr.find(".//hc:left", NS)
        left_indent = _mm(float(left_node.get("value", "0"))) if left_node is not None else 0.0
        alignment = {"CENTER": "center", "JUSTIFY": "justify", "LEFT": "left", "RIGHT": "right"}
        para_styles[para_pr.get("id", "")] = (
            f"text-align:{alignment.get(horizontal, 'justify')};line-height:{line_height};"
            f"margin-left:{left_indent:.1f}mm"
            + (";break-after:avoid;page-break-after:avoid" if keep else "")
        )
    return char_styles, para_styles


def _page_css(section: ElementTree.Element) -> str:
    page = section.find(".//hp:secPr/hp:pagePr", NS)
    if page is None:
        return "@page{size:A4;margin:20mm 30mm 15mm 30mm}"
    margin = page.find("hp:margin", NS)
    width = float(page.get("width", "59528"))
    height = float(page.get("height", "84186"))
    top = float(margin.get("top", "5668")) if margin is not None else 5668.0
    right = float(margin.get("right", "8504")) if margin is not None else 8504.0
    bottom = float(margin.get("bottom", "4252")) if margin is not None else 4252.0
    left = float(margin.get("left", "8504")) if margin is not None else 8504.0
    return (
        f"@page{{size:{_mm(width):.1f}mm {_mm(height):.1f}mm;"
        f"margin:{_mm(top):.1f}mm {_mm(right):.1f}mm "
        f"{_mm(bottom):.1f}mm {_mm(left):.1f}mm}}"
    )


def _render_picture(
    picture: ElementTree.Element,
    char_styles: dict[str, dict[str, str]],
    images: dict[str, bytes],
) -> str:
    del char_styles
    size = picture.find("hp:curSz", NS)
    width = float(size.get("width", "0")) if size is not None else 0.0
    height = float(size.get("height", "0")) if size is not None else 0.0
    image = picture.find(".//hc:img", NS)
    reference = image.get("binaryItemIDRef", "") if image is not None else ""
    payload = images.get(reference, b"")
    encoded = base64.b64encode(payload).decode("ascii") if payload else ""
    image_html = (
        f'<img alt="{html.escape(reference)}" src="data:image/png;base64,{encoded}" '
        f'style="width:{_mm(width):.2f}mm;height:{_mm(height):.2f}mm">'
        if encoded
        else f'<span class="missing">[missing image {html.escape(reference)}]</span>'
    )
    caption = picture.find("hp:caption", NS)
    caption_html = ""
    if caption is not None:
        text = " ".join("".join(node.itertext()) for node in caption.iter(f"{{{HP}}}t"))
        caption_html = f'<div class="caption">{html.escape(text)}</div>'
    position = picture.find("hp:pos", NS)
    floating = position is not None and position.get("treatAsChar") == "0"
    class_name = "figure floating" if floating else "figure"
    return f'<div class="{class_name}">{image_html}{caption_html}</div>'


def _render_paragraph(
    paragraph: ElementTree.Element,
    char_styles: dict[str, dict[str, str]],
    para_styles: dict[str, str],
    images: dict[str, bytes],
) -> str:
    pieces: list[str] = []
    for run in paragraph.findall("hp:run", NS):
        style = char_styles.get(run.get("charPrIDRef", "0"), {})
        css = ";".join(
            f"{key}:{value}" for key, value in style.items() if value and key != "transform"
        )
        for child in run:
            tag = child.tag.split("}")[-1]
            if tag == "t":
                pieces.append(f'<span style="{css}">{html.escape("".join(child.itertext()))}</span>')
            elif tag == "pic":
                pieces.append(_render_picture(child, char_styles, images))
            elif tag == "tbl":
                pieces.append(_render_table(child, char_styles, images))
    style = para_styles.get(
        paragraph.get("paraPrIDRef", "0"), "text-align:justify;line-height:1.6"
    )
    if paragraph.get("pageBreak") == "1":
        style += ";page-break-before:always"
    body = "".join(pieces)
    return f'<p style="{style}">{body if body.strip() else "&nbsp;"}</p>'


def _render_table(
    table: ElementTree.Element,
    char_styles: dict[str, dict[str, str]],
    images: dict[str, bytes],
) -> str:
    rows: list[str] = []
    source_rows = table.findall("hp:tr", NS)
    for row in source_rows:
        cells: list[str] = []
        for cell in row.findall("hp:tc", NS):
            inner = "".join(
                _render_paragraph(paragraph, char_styles, {}, images)
                for paragraph in cell.findall("hp:subList/hp:p", NS)
            )
            size = cell.find("hp:cellSz", NS)
            width = float(size.get("width", "0")) if size is not None else 0.0
            width_style = f' style="width:{_mm(width):.1f}mm"' if width else ""
            span = cell.find("hp:cellSpan", NS)
            col_span = int(span.get("colSpan", "1")) if span is not None else 1
            row_span = int(span.get("rowSpan", "1")) if span is not None else 1
            spans = (f' colspan="{col_span}"' if col_span > 1 else "") + (
                f' rowspan="{row_span}"' if row_span > 1 else ""
            )
            cells.append(f"<td{spans}{width_style}>{inner}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    first_cells = source_rows[0].findall("hp:tc", NS) if source_rows else []
    title_row = len(first_cells) == 1 or any(
        (span := cell.find("hp:cellSpan", NS)) is not None
        and int(span.get("colSpan", "1")) > 1
        for cell in first_cells
    )
    header_count = min(len(rows), 2 if title_row else 1)
    keep = ' class="keep"' if table.get("pageBreak") == "TABLE" else ""
    return (
        f"<table{keep}><thead>{''.join(rows[:header_count])}</thead>"
        f"<tbody>{''.join(rows[header_count:])}</tbody></table>"
    )


def build_html(hwpx_path: str | Path) -> str:
    """Build a self-contained paginated HTML preview."""
    with zipfile.ZipFile(hwpx_path) as archive:
        section_bytes = archive.read("Contents/section0.xml")
        header_bytes = archive.read("Contents/header.xml")
        images = {
            Path(name).stem: archive.read(name)
            for name in archive.namelist()
            if name.startswith("BinData/") and not name.endswith("/")
        }
    char_styles, para_styles = _parse_header(header_bytes)
    section = ElementTree.fromstring(section_bytes)
    paragraphs = "".join(
        _render_paragraph(paragraph, char_styles, para_styles, images)
        for paragraph in section.findall("hp:p", NS)
    )
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'><style>"
        + _page_css(section)
        + "body{margin:0;font-family:"
        + DEFAULT_FONT
        + "}p{margin:0 0 .35em;white-space:pre-wrap}"
        ".figure{margin:.6em 0;text-align:center;break-inside:avoid}"
        ".floating{float:left;clear:both;width:100%}"
        ".caption{font-size:9pt;color:#333;margin-top:.2em}"
        ".missing{color:#c00}"
        "table{border-collapse:collapse;table-layout:fixed;width:100%;margin:.4em 0;"
        "break-inside:auto}tr{break-inside:avoid}"
        "table.keep{break-inside:avoid}"
        "td{border:.5pt solid #666;padding:2pt 3pt;vertical-align:top;font-size:9pt;"
        "word-break:break-all;overflow-wrap:anywhere}"
        "</style></head><body>"
        + paragraphs
        + "</body></html>"
    )


def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, **kwargs)


def resolve_chrome(
    explicit: str | None = None, env: Mapping[str, str] | None = None
) -> str:
    if explicit is not None:
        return explicit
    environment = env if env is not None else os.environ
    configured = environment.get("KIMM_DOCBOT_CHROME", "")
    if configured:
        return configured
    for candidate in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(candidate, path=environment.get("PATH") if env is not None else None)
        if found is not None:
            return found
    raise RuntimeError("no Chrome/Chromium binary found; set KIMM_DOCBOT_CHROME")


def render_preview(
    hwpx_path: str | Path,
    out_dir: str | Path,
    *,
    chrome: str | None = None,
    pdftoppm: str = "pdftoppm",
    runner: Runner = _run,
) -> PreviewResult:
    """Write self-contained HTML, a paginated PDF, and one PNG per page."""
    source = Path(hwpx_path)
    destination = Path(out_dir)
    pages_dir = destination / "pages"
    destination.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)
    html_path = destination / "preview.html"
    pdf_path = destination / "preview.pdf"
    html_path.write_text(build_html(source), encoding="utf-8")
    chrome_result = runner(
        [
            resolve_chrome(chrome),
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            f"--print-to-pdf={pdf_path}",
            "--no-pdf-header-footer",
            html_path.resolve().as_uri(),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if chrome_result.returncode != 0:
        raise RuntimeError((chrome_result.stderr or chrome_result.stdout or "Chrome failed").strip())
    ppm_result = runner(
        [pdftoppm, "-png", "-r", "110", str(pdf_path), str(pages_dir / "page")],
        capture_output=True,
        text=True,
        check=False,
    )
    if ppm_result.returncode != 0:
        raise RuntimeError((ppm_result.stderr or ppm_result.stdout or "pdftoppm failed").strip())
    page_paths = tuple(sorted(pages_dir.glob("page-*.png")))
    if not page_paths:
        raise RuntimeError("pdftoppm reported success without page images")
    return PreviewResult(html_path, pdf_path, page_paths)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kimm-docbot-preview")
    parser.add_argument("hwpx", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--chrome", default=None, help="browser binary; else KIMM_DOCBOT_CHROME")
    args = parser.parse_args(argv)
    result = render_preview(args.hwpx, args.out_dir, chrome=args.chrome)
    print(
        f"VISUAL-PREVIEW pages={len(result.page_paths)} html={result.html_path} "
        f"pdf={result.pdf_path}"
    )
    return 0


__all__ = ["main", "resolve_chrome"]


if __name__ == "__main__":
    raise SystemExit(main())
