from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import cast

from ..contracts.collab_models import EvidenceCandidate
from ..contracts.ids import stable_id


_EXT_SOURCES_RE = re.compile(
    r"^##\s+External Sources",
    re.MULTILINE | re.IGNORECASE,
)
_VERIFIED_CLAIMS_RE = re.compile(
    r"^##\s+Verified Claims",
    re.MULTILINE | re.IGNORECASE,
)
_DETAILED_FINDINGS_RE = re.compile(
    r"^##\s+Detailed Findings",
    re.MULTILINE | re.IGNORECASE,
)

_SOURCE_LINE_RE = re.compile(r"^\s*\d+\.\s+(https?://\S+)", re.MULTILINE)

_CLAIM_ROW_RE = re.compile(
    r"^\|\s*(.+?)\s*\|\s*(CONFIRMED|REFUTED)\s*\|\s*(https?://\S+?)\s*\|",
    re.MULTILINE,
)


def convert(
    synthesis_path: Path, out_dir: Path, *, sensitivity: str = "internal"
) -> int:
    text = synthesis_path.read_text(encoding="utf-8").strip()

    if not text:
        print(f"ERROR: {synthesis_path} is empty", file=sys.stderr)
        return 1

    if not _DETAILED_FINDINGS_RE.search(text):
        print(f"ERROR: Missing 'Detailed Findings' section in {synthesis_path}", file=sys.stderr)
        return 1
    if not _EXT_SOURCES_RE.search(text):
        print(f"ERROR: Missing 'External Sources' section in {synthesis_path}", file=sys.stderr)
        return 1
    if not _VERIFIED_CLAIMS_RE.search(text):
        print(f"ERROR: Missing 'Verified Claims' section in {synthesis_path}", file=sys.stderr)
        return 1

    claims_by_url: dict[str, list[str]] = {}
    confirmed_count = 0
    for match in _CLAIM_ROW_RE.finditer(text):
        claim_text = match.group(1).strip()
        status = match.group(2).strip().upper()
        url = match.group(3).strip().rstrip("/")
        if status == "CONFIRMED":
            confirmed_count += 1
            claims_by_url.setdefault(url, []).append(claim_text)

    if confirmed_count == 0:
        print(f"ERROR: No CONFIRMED claims found in {synthesis_path}", file=sys.stderr)
        return 1

    ext_urls = {match.group(1).rstrip("/") for match in _SOURCE_LINE_RE.finditer(text)}
    for url in claims_by_url:
        if url not in ext_urls:
            print(f"ERROR: CONFIRMED source '{url}' not found in External Sources", file=sys.stderr)
            return 1

    out_dir.mkdir(parents=True, exist_ok=True)

    for url in sorted(claims_by_url):
        sha8 = stable_id(url)[:8]
        filename = f"research-{sha8}.md"
        body = "\n".join(claims_by_url[url])
        content = f"---\nsource_url: {url}\nsensitivity: {sensitivity}\n---\n{body}\n"
        _ = (out_dir / filename).write_text(content, encoding="utf-8")

    return 0


def convert_with_candidates(
    synthesis_path: Path,
    out_dir: Path,
) -> tuple[int, list[EvidenceCandidate]]:
    rc = convert(synthesis_path, out_dir)
    if rc != 0:
        return rc, []

    text = synthesis_path.read_text(encoding="utf-8").strip()
    candidates: list[EvidenceCandidate] = []
    for match in _CLAIM_ROW_RE.finditer(text):
        claim_text = match.group(1).strip()
        status = match.group(2).strip().upper()
        url = match.group(3).strip().rstrip("/")
        if status == "CONFIRMED":
            candidates.append(EvidenceCandidate.from_live(url, claim_text, "background"))

    seen: set[str] = set()
    unique: list[EvidenceCandidate] = []
    for candidate in candidates:
        if candidate.candidate_id not in seen:
            seen.add(candidate.candidate_id)
            unique.append(candidate)

    return 0, unique


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m skills.proposal.engine.converter.research_convert",
        description="Convert ultraresearch SYNTHESIS.md to corpus files",
    )
    _ = parser.add_argument("synthesis", help="Path to SYNTHESIS.md")
    _ = parser.add_argument("--out", required=True, help="Output corpus directory")
    _ = parser.add_argument(
        "--sensitivity", choices=("internal", "public"), default="internal",
        help="Sensitivity tag for emitted corpus files (default: internal)",
    )
    args = parser.parse_args(argv)

    synthesis_path = Path(cast(str, args.synthesis))
    out_dir = Path(cast(str, args.out))

    if not synthesis_path.exists():
        print(f"ERROR: {synthesis_path} not found", file=sys.stderr)
        return 1

    return convert(synthesis_path, out_dir, sensitivity=cast(str, args.sensitivity))


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["convert", "convert_with_candidates", "main"]
