"""Batch curation run: select, distill, hand each draft to the existing gate."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeAlias

from automation.twin_distill.llm import LlmClient
from automation.wiki_curate.candidates import Candidate, SourceNote, select_candidates
from automation.wiki_curate.distill import DistillRefused, distilled_body
from automation.wiki_curate.draft import DraftRefused, draft_argv
from automation.wiki_curate.state import record_proposals, remaining_quota

Runner: TypeAlias = Callable[[list[str]], int]
Clock: TypeAlias = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class CurationPlan:
    candidates: tuple[Candidate, ...]
    emitted: int
    failures: tuple[str, ...]
    quota_remaining: int


def run_curation(
    *,
    sources: Iterable[SourceNote],
    existing_digests: frozenset[str],
    existing_origins: frozenset[str] = frozenset(),
    state_path: Path,
    cli_path: Path,
    workspace: Path,
    client: LlmClient,
    runner: Runner,
    clock: Clock,
    cap: int,
    emit: bool,
) -> CurationPlan:
    quota = remaining_quota(state_path, cap=cap, clock=clock)
    candidates = select_candidates(
        sources,
        existing_digests=existing_digests,
        existing_origins=existing_origins,
        limit=quota,
        clock=clock,
    )
    if not emit:
        return CurationPlan(candidates, 0, (), quota)

    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    emitted = 0
    failures: list[str] = []
    for candidate in candidates:
        try:
            body = distilled_body(candidate, client=client)
            body_file = workspace / f"{candidate.digest[:16]}.md"
            body_file.write_text(body, encoding="utf-8")
            argv = draft_argv(candidate, cli_path=cli_path, body_file=body_file)
        except (DistillRefused, DraftRefused, OSError):
            failures.append(candidate.source_ref)
            continue
        if runner(argv) != 0:
            failures.append(candidate.source_ref)
            continue
        emitted += 1

    if emitted:
        record_proposals(state_path, emitted, clock=clock)
    return CurationPlan(
        candidates, emitted, tuple(failures), remaining_quota(state_path, cap=cap, clock=clock)
    )
