"""Deterministic text extraction for meeting inputs (md/txt/pdf).

Runs BEFORE any LLM. Size gate first, then extraction, then the caller must
run the sensitivity gate (meeting_gate) on the returned text before routing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final

MAX_INPUT_BYTES: Final = 25 * 1024 * 1024
_MIN_TEXT_CHARS: Final = 20
_TEXT_SUFFIXES: Final = frozenset({".md", ".markdown", ".txt"})

SIZE_EXCEEDED_NOTICE: Final = (
    "크기 초과: 회의록 파일은 25MiB 이하만 처리합니다. "
    "파일을 나누거나 텍스트(md/txt)로 변환해 다시 올려주세요."
)
MANUAL_CONVERSION_NOTICE: Final = (
    "수동 변환 요청: 이 PDF에는 텍스트 레이어가 없습니다(스캔본/이미지). "
    "OCR 등으로 텍스트(md/txt)로 변환한 뒤 다시 올려주세요. 내용은 추측하지 않습니다."
)
UNSUPPORTED_NOTICE: Final = "지원하지 않는 형식입니다. md/txt/pdf만 처리합니다."


class ExtractionRefused(Exception):
    """Deterministic refusal with a user-facing Korean notice and exit code."""

    def __init__(self, notice: str, exit_code: int) -> None:
        super().__init__(notice)
        self.notice = notice
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class ExtractedText:
    """Extraction result: normalized text plus the detected input kind."""

    text: str
    kind: str  # "md" | "txt" | "pdf"
    input_bytes: int


PdfRunner = Callable[[Path], str]


def run_pdftotext(path: Path) -> str:
    """Extract the text layer via poppler pdftotext (no OCR, no fabrication)."""
    completed = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", "-layout", str(path), "-"],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise ExtractionRefused(MANUAL_CONVERSION_NOTICE, exit_code=4)
    return completed.stdout.decode("utf-8", errors="replace")


def check_size(path: Path, *, max_bytes: int = MAX_INPUT_BYTES) -> int:
    """Reject oversized inputs before reading a single content byte."""
    size = path.stat().st_size
    if size > max_bytes:
        raise ExtractionRefused(SIZE_EXCEEDED_NOTICE, exit_code=3)
    return size


def _has_text_layer(text: str) -> bool:
    meaningful = [ch for ch in text if not ch.isspace() and ch != "\x0c"]
    return len(meaningful) >= _MIN_TEXT_CHARS


def extract_file(path: Path, *, pdf_runner: PdfRunner = run_pdftotext) -> ExtractedText:
    """Size-gate then extract text from a md/txt/pdf file."""
    size = check_size(path)
    suffix = path.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        kind = "txt" if suffix == ".txt" else "md"
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _has_text_layer(text):
            raise ExtractionRefused(
                "빈 문서입니다: 회의 내용이 담긴 파일을 올려주세요.", exit_code=5
            )
        return ExtractedText(text=text, kind=kind, input_bytes=size)
    if suffix == ".pdf":
        text = pdf_runner(path)
        if not _has_text_layer(text):
            raise ExtractionRefused(MANUAL_CONVERSION_NOTICE, exit_code=4)
        return ExtractedText(text=text, kind="pdf", input_bytes=size)
    raise ExtractionRefused(UNSUPPORTED_NOTICE, exit_code=5)


def extract_body(body: str) -> ExtractedText:
    """Wrap an inline `!meeting` command body as extracted text."""
    encoded = body.encode("utf-8")
    if len(encoded) > MAX_INPUT_BYTES:
        raise ExtractionRefused(SIZE_EXCEEDED_NOTICE, exit_code=3)
    if not _has_text_layer(body):
        raise ExtractionRefused(
            "본문이 비어 있습니다: `!meeting` 뒤에 회의 내용을 붙여주세요.", exit_code=5
        )
    return ExtractedText(text=body, kind="txt", input_bytes=len(encoded))
