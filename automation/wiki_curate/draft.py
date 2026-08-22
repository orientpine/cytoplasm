"""Build the argv that hands a candidate to the existing wiki draft gate."""

from __future__ import annotations

from pathlib import Path

from automation.wiki_curate.candidates import Candidate

DRAFT_KIND = "note"
DRAFT_AUTHORITY = "advisory"
DRAFT_PROVENANCE = "inferred"
"""Distilled proposals are advisory and inferred; `strict` belongs to stated rules only."""


class DraftRefused(RuntimeError):
    """The candidate may not be handed to the gate."""


def draft_argv(
    candidate: Candidate,
    *,
    cli_path: Path,
    body_file: Path,
    channel_id: str = "dm",
) -> list[str]:
    if not candidate.review_after:
        raise DraftRefused(f"review_after is mandatory: {candidate.source_ref}")
    if not candidate.title.strip():
        raise DraftRefused(f"title is mandatory: {candidate.source_ref}")
    argv = [
        str(cli_path),
        "draft",
        "--title",
        candidate.title,
        "--tags",
        ",".join(candidate.tags),
        "--kind",
        DRAFT_KIND,
        "--authority",
        DRAFT_AUTHORITY,
        "--provenance",
        DRAFT_PROVENANCE,
        "--review-after",
        candidate.review_after,
        "--channel-id",
        channel_id,
        "--body-file",
        str(body_file),
    ]
    if candidate.entity:
        argv += ["--entity", ",".join(candidate.entity)]
    if candidate.relations:
        argv += ["--relations", ",".join(candidate.relations)]
    if candidate.event_date:
        argv += ["--event-date", candidate.event_date]
    return argv
