"""In-process HWPX render entry point for the proposal skill.

The skill used to reach an external kimm-docbot checkout at a pinned SHA over
``uv run``. The engine lives in this repository now, so the skill calls it
directly; the checkout, the pin, and the block it raised are gone with it.
``engine_digest`` replaces the pin: provenance now comes from the engine's own
bytes rather than from another repository's HEAD.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from .contracts.models import RunResult
from .pipeline import orchestrator

ENGINE_ROOT = Path(__file__).resolve().parent


def render_hwpx(
    *,
    drafts_path: str | Path,
    corpus_dir: str | Path,
    out_path: str | Path,
    profile: str | None = None,
    images_dir: str | Path | None = None,
    figures_path: str | Path | None = None,
    tables_path: str | Path | None = None,
    cover_overrides: Mapping[str, str] | None = None,
    project: str = "",
) -> RunResult:
    """Render a drafts bundle into an HWPX artifact at ``out_path``."""
    return orchestrator.render(
        drafts_path=str(drafts_path),
        corpus_dir=str(corpus_dir),
        out_path=str(out_path),
        profile=profile,
        images_dir=Path(images_dir) if images_dir is not None else None,
        figures_path=Path(figures_path) if figures_path is not None else None,
        tables_path=Path(tables_path) if tables_path is not None else None,
        cover_overrides=dict(cover_overrides) if cover_overrides is not None else None,
        project=project,
    )


def engine_digest() -> str:
    """Content digest of engine sources, shared layout contract, and seed form.

    This is what the artifact manifest records instead of an external HEAD: it
    is reproducible from the checkout in hand, so a reader can tell which engine
    produced an artifact without another repository being present.
    """
    digest = hashlib.sha256()
    sources = sorted(ENGINE_ROOT.rglob("*.py"))
    forms = sorted(ENGINE_ROOT.glob("resource/*.hwpx"))
    for path in sources + forms:
        digest.update(path.relative_to(ENGINE_ROOT).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    digest.update(b"../layout_profile.py")
    digest.update((ENGINE_ROOT.parent / "layout_profile.py").read_bytes())
    return digest.hexdigest()


__all__ = ["ENGINE_ROOT", "engine_digest", "render_hwpx"]
