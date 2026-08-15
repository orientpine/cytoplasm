"""Turn durable-judgment memory entries into decision-twin draft proposals.

The curator is a *proposer*, so per SI-3 it caps its own drafts at
``provenance: observed`` / ``authority: advisory`` — cha's gate ✅ is what
activates them (and may upgrade the authority).  This module is pure: it
builds the draft args deterministically and idempotently.  Legacy proposal
deduplication uses a whitespace-insensitive content hash, while deletion is
bound to the exact source-qualified entry bytes.  Actually posting the draft
through ``wiki_cli`` and the shared wiki gate happens in the cron watcher.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from . import binding
from .model import MemoryEntry, MemoryKind

TwinKind = Literal["principle", "preference", "decision"]

_DECISION_CUES: tuple[str, ...] = ("결정", "정했", "하기로 했", "로 정한다")
_PREFERENCE_CUES: tuple[str, ...] = ("선호", "좋아", "싫어", "취향")

_NOTE = "(자체 메모리에서 자동 승격 — cha 확인 시 보완)"


@dataclass(frozen=True, slots=True)
class PromotionProposal:
    source_kind: MemoryKind
    entry_text: str
    twin_kind: TwinKind
    authority: Literal["advisory"]
    provenance: Literal["observed"]
    slug: str
    title: str
    body: str
    entry_digest: str
    promotion_key: str


def content_hash(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def infer_twin_kind(text: str) -> TwinKind:
    if any(cue in text for cue in _DECISION_CUES):
        return "decision"
    if any(cue in text for cue in _PREFERENCE_CUES):
        return "preference"
    return "principle"


def _body(twin_kind: TwinKind, text: str) -> str:
    if twin_kind == "preference":
        return f"## Preference\n{text}\n\n## Boundary\n{_NOTE}"
    if twin_kind == "decision":
        return (
            f"## Context\n{_NOTE}\n\n## Decision\n{text}\n\n"
            f"## Rationale & Trade-offs\n{_NOTE}\n\n## What would change my mind\n{_NOTE}"
        )
    return f"## Trigger\n{_NOTE}\n\n## Rule\n{text}\n\n## Exceptions\n{_NOTE}"


def build_proposal(entry_text: str, *, source_kind: MemoryKind) -> PromotionProposal:
    twin_kind = infer_twin_kind(entry_text)
    digest = binding.entry_digest(source_kind, entry_text)
    key = binding.promotion_key(source_kind, digest)
    slug = binding.promoted_slug(source_kind, digest)
    summary = " ".join(entry_text.split())[:24]
    marker = binding.render_marker(
        binding.DeletionMarker(
            version=binding.MARKER_VERSION,
            promotion_key=key,
            source_kind=source_kind,
            entry_digest=digest,
            delete_after_persist=True,
        )
    )
    return PromotionProposal(
        source_kind=source_kind,
        entry_text=entry_text,
        twin_kind=twin_kind,
        authority="advisory",
        provenance="observed",
        slug=slug,
        title=f"메모리 승격(승인 시 자체 메모리에서 삭제): {summary}",
        body=f"{_body(twin_kind, entry_text)}\n{marker}",
        entry_digest=digest,
        promotion_key=key,
    )


def new_proposals(
    candidates: tuple[MemoryEntry, ...],
    already_proposed: set[str],
    *,
    source_kind: MemoryKind,
) -> tuple[PromotionProposal, ...]:
    seen = set(already_proposed)
    proposals: list[PromotionProposal] = []
    for entry in candidates:
        digest = content_hash(entry.text)
        if digest in seen:
            continue
        seen.add(digest)
        proposals.append(build_proposal(entry.text, source_kind=source_kind))
    return tuple(proposals)
