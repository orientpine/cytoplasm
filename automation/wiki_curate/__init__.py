"""Obsidian→wiki curation: proposes candidates, never writes a note.

The wiki holds owner-approved judgments; Obsidian holds the raw notes they are
distilled from. This package closes that gap by proposing candidates in batches
on demand. Every proposal still leaves through the existing ``wiki_cli draft``
gate and becomes a note only after the owner reacts ✅ — there is no watcher, no
new approval surface, and no write path here.
"""

from __future__ import annotations

from automation.wiki_curate.candidates import Candidate, SourceNote, content_digest, select_candidates
from automation.wiki_curate.draft import DraftRefused, draft_argv
from automation.wiki_curate.state import StateRefused, record_proposals, remaining_quota

__all__ = [
    "Candidate",
    "DraftRefused",
    "SourceNote",
    "StateRefused",
    "content_digest",
    "draft_argv",
    "record_proposals",
    "remaining_quota",
    "select_candidates",
]
