"""Deterministic minimal PDF fixture generator (no third-party deps).

--text    : one page WITH a text layer (ASCII, pdftotext-extractable)
--scanned : one page WITHOUT any text operator (simulates a scanned PDF)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _build_pdf(content_stream: bytes, *, with_font: bool) -> bytes:
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    resources = (
        b"/Resources << /Font << /F1 5 0 R >> >> " if with_font else b""
    )
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        + resources
        + b"/Contents 4 0 R >>"
    )
    objects.append(
        b"<< /Length "
        + str(len(content_stream)).encode()
        + b" >>\nstream\n"
        + content_stream
        + b"\nendstream"
    )
    if with_font:
        objects.append(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    count = len(objects) + 1
    out += f"xref\n0 {count}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def text_pdf(lines: list[str]) -> bytes:
    """PDF with a real text layer."""
    ops = [b"BT /F1 12 Tf 72 720 Td 16 TL"]
    for line in lines:
        safe = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        ops.append(b"(" + safe.encode("ascii") + b") Tj T*")
    ops.append(b"ET")
    return _build_pdf(b"\n".join(ops), with_font=True)


def scanned_pdf() -> bytes:
    """PDF with zero text operators (image-only/scanned simulation)."""
    return _build_pdf(b"0.9 g 72 72 468 648 re f", with_font=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out")
    kind = parser.add_mutually_exclusive_group(required=True)
    kind.add_argument("--text", action="store_true")
    kind.add_argument("--scanned", action="store_true")
    args = parser.parse_args(argv)
    if args.text:
        payload = text_pdf(
            [
                "W2-3 fixture meeting (pdf).",
                "Cha: prepare the dataset dictionary by 2026-07-24.",
                "Cha: draft the IRB amendment by 2026-07-31.",
                "Cha: send the summary mail to the lab by 2026-07-18.",
                "Park: review sensor firmware by 2026-07-22.",
                "Milestone: interim report submission on 2026-08-01.",
                "Milestone: conference abstract deadline on 2026-08-15.",
            ]
        )
    else:
        payload = scanned_pdf()
    Path(args.out).write_bytes(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
