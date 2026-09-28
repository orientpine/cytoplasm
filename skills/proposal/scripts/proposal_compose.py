"""The v2 ``draft`` stage: run the engine planner and writers over the current corpus.

Before this command existed the pipeline table named an "engine ``draft``" step with no
CLI behind it. On 2026-09-28 the node agent, finding nothing to run, hand-wrote
``drafts.json``, copied another proposal's plan and drew placeholder figures — the
document looked finished and read like none of the engine's work. This is that missing
step, run live through the same Hermes Codex OAuth path the engine already uses.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Final, cast

from . import proposal_config, proposal_version
from .proposal_figure_tokens import STALE_REFINE_OUTPUTS, FigureTokenError, place_figures

_DRAFT_OUTPUTS: Final = ("drafts.json", "drafts.json.planspec.json", "drafts.json.pms.json")
_PLAN_KPI_SHAPE: Final = "<지표>; baseline: 6%; target: 3%; unit: %; weight: 40%; method: …; env: …"


class ComposeError(RuntimeError):
    """The engine draft stage could not produce a drafts bundle."""


def _run_engine(corpus: Path, out: Path, profile: str) -> tuple[int, str]:
    from ..engine.pipeline.cli import main as engine_main

    previous = os.environ.get("KIMM_DOCBOT_PROFILE")
    os.environ["KIMM_DOCBOT_PROFILE"] = profile
    stream = io.StringIO()
    try:
        with redirect_stdout(stream):
            returncode = engine_main(
                ["draft", "--corpus", str(corpus), "--out", str(out), "--mode", "live"]
            )
    finally:
        if previous is None:
            _ = os.environ.pop("KIMM_DOCBOT_PROFILE", None)
        else:
            os.environ["KIMM_DOCBOT_PROFILE"] = previous
    return returncode, stream.getvalue()


def _failure_detail(events: str) -> str:
    for line in reversed(events.splitlines()):
        try:
            event = cast(object, json.loads(line))
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("event") == "pipeline_failed":
            return str(cast(dict[str, object], event).get("error", ""))[:300]
    return "engine draft failed without a pipeline_failed event"


def compose(slug: str, *, profile: str | None = None) -> dict[str, object]:
    """Draft the current version from its corpus and drop refine outputs that it obsoletes."""
    store = proposal_version.VersionStore.from_environment()
    head = store.head(slug)
    if head is None:
        raise ComposeError("proposal has no current version; run `research` first")
    version = store.resolve_slug_dir(slug) / "versions" / head
    corpus = version / "corpus"
    if corpus.is_symlink() or not corpus.is_dir() or not any(corpus.glob("*.md")):
        raise ComposeError("corpus is empty; run `research` then `corpus` first")
    selected = profile or proposal_config.load_config().profile
    out = version / "out"
    out.mkdir(mode=0o700, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".compose-", dir=out))
    try:
        returncode, events = _run_engine(corpus, scratch / "drafts.json", selected)
        if returncode != 0:
            detail = _failure_detail(events)
            if "KPI evidence" in detail:
                detail += f" — add KPI lines to inputs/PLAN.md ({_PLAN_KPI_SHAPE}) and rerun `corpus`"
            raise ComposeError(detail)
        for name in _DRAFT_OUTPUTS:
            if not (scratch / name).is_file():
                raise ComposeError(f"engine draft did not write {name}")
        for name in STALE_REFINE_OUTPUTS:
            (out / name).unlink(missing_ok=True)
        for name in _DRAFT_OUTPUTS:
            (scratch / name).chmod(0o600)
            os.replace(scratch / name, out / name)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    figures = None
    if (version / "figures.json").is_file():
        try:
            figures = place_figures(version)
        except (FigureTokenError, ValueError) as error:
            raise ComposeError(f"drafts written but figures were not placed: {error}") from error
    document = cast(dict[str, object], json.loads((out / "drafts.json").read_text("utf-8")))
    sections = cast(list[object], document.get("sections", []))
    return {
        "drafts": str(out / "drafts.json"),
        "figures": figures,
        "profile": selected,
        "sections": len(sections),
        "slug": slug,
        "version": head,
    }


def command(args: argparse.Namespace) -> int:
    """Execute the proposal CLI compose subcommand."""
    try:
        payload = compose(cast(str, args.slug), profile=cast(str | None, args.profile))
    except (ComposeError, proposal_config.ConfigError, proposal_version.VersionError) as error:
        print(f"PROPOSAL-COMPOSE-ERROR {error}", file=sys.stderr)
        return 1
    if cast(bool, args.json):
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"PROPOSAL-COMPOSED slug={payload['slug']} version={payload['version']} "
            + f"sections={payload['sections']} profile={payload['profile']}"
        )
    return 0


__all__ = ["ComposeError", "command", "compose"]
