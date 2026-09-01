"""Deep-research brief generation and deterministic synthesis contract validation.

The live launcher is intended to run on a node with Hermes installed. This module never
crawls the web itself: Hermes performs collection, while this code only writes its contract
and validates the resulting artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from . import proposal_knowledge, proposal_version

_REQUIRED_HEADINGS = ("Detailed Findings", "External Sources", "Verified Claims")
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_SOURCE_RE = re.compile(r"^\s*\d+\.\s+(https?://\S+?)(?:\s+.*)?$")
_CLAIM_RE = re.compile(
    r"^\|\s*(C\d{2,}:\s*[^|]+?)\s*\|\s*(CONFIRMED|REFUTED)\s*\|\s*"
    r"(https?://[^|\s]+)\s*\|\s*$"
)
_COVERAGE_RE = re.compile(r"^\|\s*([0-4])\s*\|\s*([^|]*)\s*\|\s*$")
_CLAIM_ID_RE = re.compile(r"^(C\d{2,}):\s*(.+)$")
_COVERAGE_IDS_RE = re.compile(r"^C\d{2,}(?:\s*,\s*C\d{2,})*$")


class ResearchError(RuntimeError):
    """Research setup or transport failed."""


class ResearchValidationError(ResearchError):
    """SYNTHESIS.md violated the deterministic research contract."""

    def __init__(self, line: int, message: str) -> None:
        self.line = line
        self.message = message
        super().__init__(f"line {line}: {message}")


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    text: str
    status: Literal["CONFIRMED"]
    url: str
    line: int


@dataclass(frozen=True, slots=True)
class ValidationResult:
    claims: tuple[Claim, ...]
    distinct_domains: int
    coverage: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class InvocationResult:
    returncode: int
    stdout: str


@dataclass(frozen=True, slots=True)
class ResearchResult:
    slug: str
    version: str
    brief: Path
    synthesis: Path


Runner = Callable[[tuple[str, ...]], InvocationResult]


def _sections(lines: list[str]) -> dict[str, tuple[int, int]]:
    headings: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        match = _HEADING_RE.fullmatch(line)
        if match is not None:
            headings.append((match.group(1), index))
    sections: dict[str, tuple[int, int]] = {}
    for position, (name, start) in enumerate(headings):
        end = headings[position + 1][1] if position + 1 < len(headings) else len(lines)
        sections[name] = (start, end)
    return sections


def _claim_rows(text: str) -> tuple[list[Claim], set[str]]:
    lines = text.splitlines()
    sections = _sections(lines)
    if "Verified Claims" not in sections:
        return [], set()
    start, end = sections["Verified Claims"]
    claims: list[Claim] = []
    all_ids: set[str] = set()
    for index in range(start + 1, end):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.lower() == "| claim | status | source |":
            continue
        if re.fullmatch(r"\|(?:\s*:?-+:?\s*\|){3}", stripped):
            continue
        match = _CLAIM_RE.fullmatch(line)
        if match is None:
            if stripped.startswith("|"):
                raise ResearchValidationError(index + 1, "malformed Verified Claims row")
            continue
        id_match = _CLAIM_ID_RE.fullmatch(match.group(1).strip())
        if id_match is None:
            raise ResearchValidationError(index + 1, "claim must begin with a claim ID")
        claim_id, claim_text = id_match.groups()
        if claim_id in all_ids:
            raise ResearchValidationError(index + 1, f"duplicate claim ID {claim_id}")
        all_ids.add(claim_id)
        if match.group(2) == "CONFIRMED":
            claims.append(Claim(claim_id, claim_text.strip(), "CONFIRMED", match.group(3), index + 1))
    return claims, all_ids


def parse_claims(text: str) -> tuple[Claim, ...]:
    """Return only CONFIRMED claims; REFUTED rows are never handed onward."""
    claims, _ = _claim_rows(text)
    return tuple(claims)


def _external_urls(lines: list[str], sections: dict[str, tuple[int, int]]) -> set[str]:
    start, end = sections["External Sources"]
    urls: set[str] = set()
    for index in range(start + 1, end):
        line = lines[index]
        match = _SOURCE_RE.fullmatch(line)
        if match is not None:
            urls.add(match.group(1).rstrip("/"))
        elif line.strip() and re.match(r"^\s*\d+\.", line):
            raise ResearchValidationError(index + 1, "malformed External Sources entry")
    return urls


def _coverage(
    lines: list[str], sections: dict[str, tuple[int, int]], confirmed_ids: set[str]
) -> tuple[tuple[str, ...], ...]:
    if "Section Coverage" not in sections:
        raise ResearchValidationError(1, "section coverage gate: missing Section Coverage")
    start, end = sections["Section Coverage"]
    mapped: dict[int, tuple[str, ...]] = {}
    for index in range(start + 1, end):
        stripped = lines[index].strip()
        if not stripped or stripped.lower() == "| section | claim ids |":
            continue
        if re.fullmatch(r"\|(?:\s*:?-+:?\s*\|){2}", stripped):
            continue
        match = _COVERAGE_RE.fullmatch(lines[index])
        if match is None:
            if stripped.startswith("|"):
                raise ResearchValidationError(index + 1, "malformed Section Coverage row")
            continue
        section = int(match.group(1))
        raw_ids = match.group(2).strip()
        if section in mapped:
            raise ResearchValidationError(index + 1, f"duplicate section coverage gate {section}")
        if not raw_ids:
            mapped[section] = ()
        elif _COVERAGE_IDS_RE.fullmatch(raw_ids) is None:
            raise ResearchValidationError(index + 1, "malformed Section Coverage claim IDs")
        else:
            mapped[section] = tuple(part.strip() for part in raw_ids.split(","))
        unknown = set(mapped[section]) - confirmed_ids
        if unknown:
            raise ResearchValidationError(
                index + 1,
                f"section coverage gate {section}: non-CONFIRMED claim {sorted(unknown)[0]}",
            )
    for section in range(5):
        if not mapped.get(section):
            raise ResearchValidationError(
                start + 1, f"section coverage gate {section}: requires a CONFIRMED claim"
            )
    return tuple(mapped[section] for section in range(5))


def validate_synthesis(path: Path) -> ValidationResult:
    """Validate headings, rows, sources, scale, domains, and five-section coverage."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ResearchValidationError(1, f"cannot read SYNTHESIS.md: {error.__class__.__name__}") from error
    if not text.strip():
        raise ResearchValidationError(1, "SYNTHESIS.md is empty")
    lines = text.splitlines()
    sections = _sections(lines)
    for heading in _REQUIRED_HEADINGS:
        if heading not in sections:
            raise ResearchValidationError(1, f"missing heading ## {heading}")

    claims, _ = _claim_rows(text)
    external_urls = _external_urls(lines, sections)
    for claim in claims:
        if claim.url.rstrip("/") not in external_urls:
            raise ResearchValidationError(
                claim.line, f"CONFIRMED URL is absent from External Sources: {claim.url}"
            )
    if len(claims) < 20:
        raise ResearchValidationError(
            sections["Verified Claims"][0] + 1,
            f"CONFIRMED claims gate: expected at least 20, found {len(claims)}",
        )
    domains = {urlsplit(claim.url).hostname for claim in claims}
    domains.discard(None)
    if len(domains) < 8:
        raise ResearchValidationError(
            sections["Verified Claims"][0] + 1,
            f"distinct domains gate: expected at least 8, found {len(domains)}",
        )
    coverage = _coverage(lines, sections, {claim.claim_id for claim in claims})
    return ValidationResult(tuple(claims), len(domains), coverage)


def _evidence_text(pack: proposal_knowledge.EvidencePack) -> str:
    if not pack.items:
        return "- 근거 없음"
    return "\n".join(
        f"- source_key={item.source_key}; bucket={item.bucket}; "
        f"sensitivity={item.sensitivity}; summary={item.summary}"
        for item in pack.items
    )


def write_research_brief(
    inputs: Path, goal: str, pack: proposal_knowledge.EvidencePack
) -> Path:
    """Write agent instructions containing summaries and source keys, never note documents."""
    inputs.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = inputs / "RESEARCH_BRIEF.md"
    content = f"""# Deep Research Brief

## Goal
{goal.strip()}

## Owner Evidence Pack
{_evidence_text(pack)}

## Required Research Scope
Cross-check public web evidence against the owner-evidence summaries above. Cover all five proposal
sections: 0 problem/current terrain, 1 target terrain and success criteria, 2 autonomous construction
method, 3 validation and risk controls, and 4 outcomes/adoption. Use at least 8 distinct web domains
and produce at least 20 CONFIRMED claims. Do not copy private note text into the synthesis; retain only
source keys and summaries when discussing personal knowledge.

## Output Contract
Write the result to the requested `SYNTHESIS.md` path. It must contain these exact headings:
`## Detailed Findings`, `## External Sources`, and `## Verified Claims`.

List every cited URL under External Sources as `1. https://...`. Use exactly this claims table shape:

| Claim | Status | Source |
| --- | --- | --- |
| C01: claim text | CONFIRMED | https://example.org/source |

Claim IDs must be unique. REFUTED is allowed but does not count. Add `## Section Coverage` with:

| Section | Claim IDs |
| --- | --- |
| 0 | C01, C06 |

Include one row for each section ID 0..4 and map each to at least one CONFIRMED claim.
"""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def _fake_synthesis(path: Path) -> None:
    urls = [f"https://research-{index % 8}.example.org/source/{index + 1}" for index in range(20)]
    lines = [
        "# Research Synthesis",
        "",
        "## Detailed Findings",
        "",
        "Evidence for all five required proposal sections is summarized by the verified claims.",
        "",
        "## External Sources",
        "",
        *(f"{index}. {url}" for index, url in enumerate(urls, start=1)),
        "",
        "## Verified Claims",
        "",
        "| Claim | Status | Source |",
        "| --- | --- | --- |",
        *(
            f"| C{index:02d}: deterministic offline evidence {index} | CONFIRMED | {url} |"
            for index, url in enumerate(urls, start=1)
        ),
        "",
        "## Section Coverage",
        "",
        "| Section | Claim IDs |",
        "| --- | --- |",
        *(f"| {section} | C{section + 1:02d} |" for section in range(5)),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _invoke(command: tuple[str, ...]) -> InvocationResult:
    environment = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"}
    try:
        completed = subprocess.run(
            command,
            cwd=Path.home(),
            env=environment,
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ResearchError(f"Hermes research invocation failed: {error.__class__.__name__}") from error
    return InvocationResult(completed.returncode, completed.stdout)


def launch_deep_research(brief: Path, synthesis: Path, runner: Runner | None = None) -> None:
    """Launch the node's Hermes deep-research agent, or deterministic offline fake transport."""
    if runner is None and os.environ.get("PROPOSAL_RESEARCH_TRANSPORT") == "fake":
        _fake_synthesis(synthesis)
        return
    prompt = (
        f"Follow the complete research instructions in {brief}. Perform web and owner-knowledge "
        f"cross-research, then write the final Markdown artifact to {synthesis}. Do not merely print it."
    )
    result = (runner or _invoke)(("hermes", "-z", prompt, "-t", "todo"))
    if result.returncode != 0:
        raise ResearchError(f"Hermes deep research failed rc={result.returncode}")
    if not synthesis.is_file():
        raise ResearchError("Hermes deep research did not write SYNTHESIS.md")


def _resolve_inputs(store: proposal_version.VersionStore, slug: str) -> tuple[str, Path]:
    version = store.head(slug)
    if version is None:
        run_key = store.compute_run_key("", [], {"stage": "research-bootstrap"}, "", "research", {})
        staged = store.begin(slug, run_key)
        if isinstance(staged, proposal_version.Reused):
            version = staged.version
        else:
            version = store.promote(
                slug,
                staged,
                {"parent": None, "request": {"stage": "research-bootstrap"}, "schema_version": 1},
            )
    inputs = store.resolve_slug_dir(slug) / "versions" / version / "inputs"
    return version, inputs


def command(args: argparse.Namespace) -> int:
    """Execute the proposal CLI research subcommand."""
    try:
        store = proposal_version.VersionStore.from_environment()
        version, inputs = _resolve_inputs(store, args.slug)
        synthesis = inputs / "SYNTHESIS.md"
        if not args.validate_only:
            if not args.goal or not args.goal.strip():
                raise ResearchError("--goal is required unless --validate-only is used")
            pack = proposal_knowledge.gather_owner_evidence(args.goal)
            brief = write_research_brief(inputs, args.goal, pack)
            launch_deep_research(brief, synthesis)
        else:
            brief = inputs / "RESEARCH_BRIEF.md"
        validate_synthesis(synthesis)
        result = ResearchResult(args.slug, version, brief, synthesis)
        payload = {
            "brief": str(result.brief),
            "slug": result.slug,
            "synthesis": str(result.synthesis),
            "version": result.version,
        }
        print(json.dumps(payload, sort_keys=True) if args.json else payload)
        return 0
    except ResearchValidationError as error:
        print(f"RESEARCH-VALIDATION line {error.line}: {error.message}", file=sys.stderr)
        return 3
    except (ResearchError, proposal_version.VersionError, ValueError) as error:
        print(f"RESEARCH-ERROR {error}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a proposal research synthesis")
    parser.add_argument("--synthesis", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        validate_synthesis(args.synthesis)
        return 0
    except ResearchValidationError as error:
        print(f"RESEARCH-VALIDATION line {error.line}: {error.message}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
