"""Build an atomic, lint-gated proposal corpus from research and owner summaries."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from . import proposal_config, proposal_knowledge, proposal_research, proposal_version
from .proposal_route_guard import RouteRefused, assert_route_allowed

_BRIEF_ITEM = re.compile(
    r"^- source_key=(.*?); bucket=([^;]+); "
    + r"(?:sensitivity=([^;]+); )?summary=(.*)$"
)


class CorpusError(RuntimeError):
    """Corpus construction or its external converter failed."""


class CorpusLintError(CorpusError):
    """The hard corpus lint gate rejected the candidate web evidence."""


@dataclass(frozen=True, slots=True)
class InvocationResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[tuple[str, ...], Path], InvocationResult]


def _invoke(argv: tuple[str, ...], cwd: Path) -> InvocationResult:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CorpusError(f"KD invocation failed: {error.__class__.__name__}") from error
    return InvocationResult(completed.returncode, completed.stdout, completed.stderr)


def _docbot_root() -> Path:
    values = dict(os.environ)
    _ = values.setdefault("PROPOSAL_DOCBOT_PIN", "0" * 40)
    return proposal_config.load_config(values).docbot_root


def _pack_from_brief(path: Path) -> proposal_knowledge.EvidencePack:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise CorpusError(f"owner evidence snapshot is unavailable: {error.__class__.__name__}") from error
    items: list[proposal_knowledge.EvidenceItem] = []
    for line in lines:
        match = _BRIEF_ITEM.fullmatch(line)
        if match is None:
            continue
        source_key, bucket, sensitivity, summary = match.groups()
        if bucket not in {"rag", "wiki-twin", "obsidian", "research-trends"}:
            raise CorpusError(f"invalid owner evidence bucket: {bucket}")
        if sensitivity is None:
            sensitivity = "owner-private"
        if sensitivity not in {"public", "owner-private", "patent-sensitive"}:
            raise CorpusError(f"invalid owner evidence sensitivity: {sensitivity}")
        items.append(proposal_knowledge.EvidenceItem(
            source_key,
            cast(proposal_knowledge.Bucket, bucket),
            summary,
            cast(proposal_knowledge.Sensitivity, sensitivity),
            None,
            None,
        ))
    return proposal_knowledge.EvidencePack("snapshot", tuple(items), (), ())


def _safe_source_key(source_key: str) -> str:
    if not source_key or "\n" in source_key or "\r" in source_key:
        raise CorpusError("owner evidence has an invalid source key")
    return source_key


def _write_owner_evidence(
    directory: Path, pack: proposal_knowledge.EvidencePack
) -> tuple[Path, ...]:
    paths: list[Path] = []
    for item in pack.items:
        source_key = _safe_source_key(item.source_key)
        sensitivity = "patent-sensitive" if item.sensitivity == "patent-sensitive" else "internal"
        digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:8]
        path = directory / f"owner-{digest}.md"
        content = "".join((
            "---\n",
            f"source_key: {source_key}\n",
            f"sensitivity: {sensitivity}\n",
            "---\n",
            f"{item.summary}\n",
        ))
        _ = path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
        paths.append(path)
    return tuple(paths)


def _is_public_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or hostname is None:
        return False
    try:
        return ipaddress.ip_address(hostname).is_global
    except ValueError:
        return "." in hostname and not hostname.endswith(".local")


def _check_claims(synthesis: Path) -> tuple[proposal_research.Claim, ...]:
    try:
        claims = proposal_research.parse_claims(synthesis.read_text(encoding="utf-8"))
    except OSError as error:
        raise CorpusError(f"cannot read SYNTHESIS.md: {error.__class__.__name__}") from error
    if not claims:
        raise CorpusError("SYNTHESIS.md has no CONFIRMED claims")
    for claim in claims:
        if not _is_public_url(claim.url):
            raise proposal_research.ResearchValidationError(
                claim.line, f"CONFIRMED URL is not publicly eligible: {claim.url}"
            )
        _ = assert_route_allowed(claim.text, "render", classification="public")
    return claims


def build_corpus(
    synthesis: Path,
    corpus: Path,
    pack: proposal_knowledge.EvidencePack,
    docbot_root: Path,
    *,
    runner: Runner | None = None,
) -> tuple[Path, ...]:
    """Convert and lint web claims, then atomically publish web and owner summaries."""
    _ = _check_claims(synthesis)
    if corpus.exists():
        if corpus.is_symlink() or not corpus.is_dir():
            raise CorpusError("corpus target is not a regular directory")
        shutil.rmtree(corpus)
    corpus.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".corpus-", dir=corpus.parent))
    candidate = temporary / "candidate"
    baseline = temporary / "baseline"
    output = temporary / "output"
    for directory in (candidate, baseline, output):
        directory.mkdir(mode=0o700)
    execute = runner or _invoke
    try:
        converted = execute(
            (
                "uv", "run", "kimm-docbot", "research-convert", str(synthesis),
                "--out", str(candidate), "--sensitivity", "public",
            ),
            docbot_root,
        )
        if converted.returncode != 0:
            raise CorpusError(f"research-convert failed rc={converted.returncode}")
        linted = execute(
            (
                "uv", "run", "kimm-docbot", "corpus-lint", "--corpus", str(baseline),
                "--candidate-dir", str(candidate),
            ),
            docbot_root,
        )
        if linted.returncode != 0:
            raise CorpusLintError(f"corpus-lint failed rc={linted.returncode}")
        web = sorted(candidate.glob("research-*.md"))
        if not web:
            raise CorpusError("research-convert emitted no corpus files")
        for path in web:
            destination = output / path.name
            os.replace(path, destination)
            destination.chmod(0o600)
        _ = _write_owner_evidence(output, pack)
        os.replace(output, corpus)
        corpus.chmod(0o700)
        return tuple(sorted(corpus.glob("*.md")))
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def command(args: argparse.Namespace, *, runner: Runner | None = None) -> int:
    """Execute the proposal CLI corpus subcommand."""
    corpus: Path | None = None
    try:
        slug = cast(str, args.slug)
        json_output = cast(bool, args.json)
        store = proposal_version.VersionStore.from_environment()
        version = store.head(slug)
        if version is None:
            raise CorpusError("proposal has no current version")
        version_dir = store.resolve_slug_dir(slug) / "versions" / version
        inputs = version_dir / "inputs"
        corpus = version_dir / "corpus"
        _ = proposal_research.validate_synthesis(inputs / "SYNTHESIS.md")
        pack = _pack_from_brief(inputs / "RESEARCH_BRIEF.md")
        files = build_corpus(
            inputs / "SYNTHESIS.md", corpus, pack, _docbot_root(), runner=runner
        )
        payload = {
            "corpus": str(corpus),
            "files": [str(path) for path in files],
            "slug": slug,
            "version": version,
        }
        print(json.dumps(payload, sort_keys=True) if json_output else payload)
        return 0
    except (CorpusLintError, proposal_research.ResearchValidationError, RouteRefused) as error:
        if corpus is not None and corpus.is_dir() and not corpus.is_symlink():
            shutil.rmtree(corpus)
        print(f"CORPUS-VALIDATION {error}", file=sys.stderr)
        return 3
    except (CorpusError, proposal_config.ConfigError, proposal_version.VersionError) as error:
        print(f"CORPUS-ERROR {error}", file=sys.stderr)
        return 1
