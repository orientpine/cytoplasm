"""Create page images from the current HWPX without mutating its version."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .proposal_config import ConfigError, ProposalConfig, load_config
from .proposal_version import VersionStore

class VisualReviewError(RuntimeError):
    """The page-image review artifact could not be produced."""


@dataclass(frozen=True, slots=True)
class VisualReviewResult:
    slug: str
    version: str
    hwpx_sha256: str
    output_dir: Path
    html_path: Path
    pdf_path: Path
    page_paths: tuple[Path, ...]
    reused: bool


class Runner(Protocol):
    """The preview seam: the in-tree engine in production, a fake under test."""

    def __call__(self, argv: list[str]) -> int: ...


def _run(argv: list[str]) -> int:
    from ..engine.pipeline.cli import main as engine_main

    return engine_main(argv)


def _complete_preview(output_dir: Path) -> tuple[Path, Path, tuple[Path, ...]] | None:
    html_path = output_dir / "preview.html"
    pdf_path = output_dir / "preview.pdf"
    page_paths = tuple(sorted((output_dir / "pages").glob("page-*.png")))
    if (
        html_path.is_file()
        and not html_path.is_symlink()
        and pdf_path.is_file()
        and not pdf_path.is_symlink()
        and page_paths
        and all(path.is_file() and not path.is_symlink() for path in page_paths)
    ):
        return html_path, pdf_path, page_paths
    return None


def run_visual_review(
    slug: str,
    *,
    config: ProposalConfig | None = None,
    runner: Runner = _run,
) -> VisualReviewResult:
    """Render the current immutable HWPX into a digest-keyed QA directory."""
    try:
        cfg = load_config() if config is None else config
    except ConfigError as error:
        print(f"CONFIG-ERROR: {error}", file=sys.stderr)
        raise SystemExit(4) from error
    store = VersionStore.from_environment()
    version = store.head(slug)
    if version is None:
        raise VisualReviewError("proposal has no current version")
    version_path = store.resolve_slug_dir(slug) / "versions" / version
    hwpx_path = version_path / "out" / "proposal.hwpx"
    if hwpx_path.is_symlink() or not hwpx_path.is_file():
        raise VisualReviewError("current proposal version has no rendered HWPX")
    digest = hashlib.sha256(hwpx_path.read_bytes()).hexdigest()
    output_dir = cfg.state_root / "visual-reviews" / slug / version / digest
    complete = _complete_preview(output_dir)
    if complete is not None:
        html_path, pdf_path, page_paths = complete
        return VisualReviewResult(
            slug, version, digest, output_dir, html_path, pdf_path, page_paths, True
        )
    if output_dir.exists():
        if output_dir.is_symlink():
            raise VisualReviewError("visual review output directory must not be a symlink")
        shutil.rmtree(output_dir)

    argv = ["preview", str(hwpx_path), "--out-dir", str(output_dir)]
    preview_chrome = os.environ.get("PROPOSAL_PREVIEW_CHROME", "").strip()
    if preview_chrome:
        argv += ["--chrome", preview_chrome]
    try:
        returncode = runner(argv)
    except (OSError, ValueError, RuntimeError) as error:
        raise VisualReviewError(f"visual preview could not run: {error}") from error
    if returncode != 0:
        raise VisualReviewError(f"visual preview failed rc={returncode}")
    complete = _complete_preview(output_dir)
    if complete is None:
        raise VisualReviewError("preview reported success without complete page artifacts")
    html_path, pdf_path, page_paths = complete
    return VisualReviewResult(
        slug, version, digest, output_dir, html_path, pdf_path, page_paths, False
    )


def command(args: argparse.Namespace) -> int:
    try:
        result = run_visual_review(args.slug)
    except VisualReviewError as error:
        print(f"PROPOSAL-VISUAL-REVIEW-ERROR {error}", file=sys.stderr)
        return 1
    if args.json:
        payload = asdict(result)
        payload["output_dir"] = str(result.output_dir)
        payload["html_path"] = str(result.html_path)
        payload["pdf_path"] = str(result.pdf_path)
        payload["page_paths"] = [str(path) for path in result.page_paths]
        payload["pages"] = payload.pop("page_paths")
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"PROPOSAL-VISUAL-REVIEW slug={result.slug} version={result.version} "
            f"pages={len(result.page_paths)} output={result.output_dir} reused={result.reused}"
        )
        for page in result.page_paths:
            print(f"PAGE {page}")
    return 0
