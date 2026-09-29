"""The v2 ``draft`` stage: run the engine planner and writers over the current corpus.

Before this command existed the pipeline table named an "engine ``draft``" step with no
CLI behind it. On 2026-09-28 the node agent, finding nothing to run, hand-wrote
``drafts.json``, copied another proposal's plan and drew placeholder figures — the
document looked finished and read like none of the engine's work. This is that missing
step, run live through the same Hermes Codex OAuth path the engine already uses.

A draft takes tens of minutes of model calls, and on 2026-09-29 a release convergence
restarted the gateway eight minutes in: the tool subprocess was killed, nothing was
kept, and a rerun would have paid for every call again. Every completed call is now
recorded in the version's resume cache, so rerunning the same command after any
interruption replays those answers and asks the model only for what is left.
"""

from __future__ import annotations

import argparse
import fcntl
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
RESUME_CACHE: Final = ".llm-resume-cache.json"
_LOCK: Final = ".compose.lock"
_SCRATCH_GLOB: Final = ".compose-*"


class ComposeError(RuntimeError):
    """The engine draft stage could not produce a drafts bundle."""


def _run_engine(corpus: Path, out: Path, profile: str, cache: Path) -> tuple[int, str]:
    from ..engine.pipeline.cli import main as engine_main

    previous = os.environ.get("KIMM_DOCBOT_PROFILE")
    os.environ["KIMM_DOCBOT_PROFILE"] = profile
    stream = io.StringIO()
    try:
        with redirect_stdout(stream):
            returncode = engine_main(
                [
                    "draft",
                    "--corpus",
                    str(corpus),
                    "--out",
                    str(out),
                    "--mode",
                    "record",
                    "--cache",
                    str(cache),
                ]
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


def _resumable_calls(cache: Path) -> int:
    """Count answers an interrupted run left behind; discard a cache that cannot be read."""
    from ..engine.agents.llm import load_mock_responses

    if not cache.exists():
        return 0
    try:
        return len(load_mock_responses(cache))
    except (AttributeError, OSError, ValueError):
        cache.unlink(missing_ok=True)
        return 0


def _hold_version_lock(out: Path) -> int:
    descriptor = os.open(out / _LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise ComposeError(
            "another compose is still running for this version; wait for it to finish"
        ) from error
    return descriptor


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
    cache = out / RESUME_CACHE
    lock = _hold_version_lock(out)
    try:
        for stale in out.glob(_SCRATCH_GLOB):
            shutil.rmtree(stale, ignore_errors=True)
        resumed = _resumable_calls(cache)
        scratch = Path(tempfile.mkdtemp(prefix=".compose-", dir=out))
        try:
            returncode, events = _run_engine(corpus, scratch / "drafts.json", selected, cache)
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
            cache.unlink(missing_ok=True)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    finally:
        os.close(lock)
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
        "resumed_llm_calls": resumed,
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
            + f"sections={payload['sections']} profile={payload['profile']} "
            + f"resumed_llm_calls={payload['resumed_llm_calls']}"
        )
    return 0


__all__ = ["ComposeError", "command", "compose"]
