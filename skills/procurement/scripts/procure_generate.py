"""Format-specific draft generation + read-back verification (W4-4)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from skills.procurement.scripts.procure_core import PLACEHOLDER, Preflight
from skills.procurement.scripts import procure_docx, procure_hwpx


class DependencyMissing(RuntimeError):
    """Required doc library absent — refuse generation (exit 7)."""


class VerifyFailed(AssertionError):
    """Generated file failed the required-field read-back assert (exit 8)."""


def _fill(text: str, fields: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda m: fields.get(m[1].strip(), m[0]), text)


def _import(module: str, pip_name: str):
    try:
        return __import__(module)
    except ImportError:
        raise DependencyMissing(
            f"DEPENDENCY-MISSING {pip_name} 미설치 — {module} 경로 생성 불가 "
            "(설치: uv pip install openpyxl)"
        ) from None


def generate(
    preflight: Preflight, template: Path, fields: dict[str, str], out: Path, form_map: Path | None = None
) -> None:
    {"docx": _generate_docx, "xlsx": _generate_xlsx, "hwpx": _generate_hwpx}[preflight.format](
        template, fields, out, form_map
    )


def verify(preflight: Preflight, generated: Path, fields: dict[str, str]) -> list[str]:
    """Assert every field VALUE is present and no {{placeholder}} remains."""
    text = {"docx": _text_docx, "xlsx": _text_xlsx, "hwpx": _text_hwpx}[preflight.format](generated)
    problems = [f"placeholder 잔존: {m[0]}" for m in PLACEHOLDER.finditer(text)]
    problems += [f"필드 값 부재: {name}={value!r}" for name, value in fields.items() if value not in text]
    if problems:
        raise VerifyFailed("; ".join(problems))
    return sorted(fields)


def _generate_docx(template: Path, fields: dict[str, str], out: Path, _form_map: Path | None) -> None:
    procure_docx.fill(template, fields, out)


def _text_docx(path: Path) -> str:
    return procure_docx.text_content(path)


# ---------------------------------------------------------------- xlsx (openpyxl)
def _generate_xlsx(template: Path, fields: dict[str, str], out: Path, _form_map: Path | None) -> None:
    openpyxl = _import("openpyxl", "openpyxl")
    workbook = openpyxl.load_workbook(str(template))
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and "{{" in cell.value:
                    cell.value = _fill(cell.value, fields)
    workbook.save(str(out))


def _text_xlsx(path: Path) -> str:
    openpyxl = _import("openpyxl", "openpyxl")
    workbook = openpyxl.load_workbook(str(path), read_only=True)
    chunks = [
        str(cell.value)
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
        if cell.value is not None
    ]
    workbook.close()
    return "\n".join(chunks)


def _generate_hwpx(template: Path, fields: dict[str, str], out: Path, form_map: Path | None) -> None:
    procure_hwpx.fill(template, fields, out, form_map)


def _text_hwpx(path: Path) -> str:
    chunks: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for member in archive.namelist():
            if member.startswith("Contents/") and member.endswith(".xml"):
                root = ET.fromstring(archive.read(member))
                chunks.extend(node.strip() for node in root.itertext() if node.strip())
    return "\n".join(chunks)
