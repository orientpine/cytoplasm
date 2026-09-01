"""Create page images from the current HWPX without mutating its version."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .proposal_config import ConfigError, ProposalConfig, load_config, preflight
from .proposal_version import VersionStore

_PREVIEW_TIMEOUT_SECONDS = 300
_CHILD_ENV_NAMES = ("HOME", "KIMM_DOCBOT_CHROME", "PATH", "PROPOSAL_PREVIEW_CHROME", "UV_CACHE_DIR")


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
    def __call__(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]: ...


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    capture_output: bool,
    text: bool,
    timeout: int,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
        check=check,
    )


def _child_environment(values: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if values is None else values
    return {name: source[name] for name in _CHILD_ENV_NAMES if name in source}


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
    report = preflight(cfg)
    if not report.ok:
        print(f"ENGINE-PIN-BLOCK: {', '.join(report.reasons)}", file=sys.stderr)
        raise SystemExit(4)

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

    argv = [
        "uv",
        "run",
        "kimm-docbot",
        "preview",
        str(hwpx_path),
        "--out-dir",
        str(output_dir),
    ]
    preview_chrome = os.environ.get("PROPOSAL_PREVIEW_CHROME", "").strip()
    if preview_chrome:
        argv += ["--chrome", preview_chrome]
    try:
        completed = runner(
            argv,
            cwd=cfg.docbot_root,
            env=_child_environment(),
            capture_output=True,
            text=True,
            timeout=_PREVIEW_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VisualReviewError(f"visual preview could not run: {error}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "no process output").strip()[:500]
        raise VisualReviewError(
            f"kimm-docbot preview failed rc={completed.returncode}: {detail}"
        )
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
