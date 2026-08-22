"""Assembly and final-review persistence for protected proposal workspaces."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import proposal_knowledge
from .proposal_storage import ProposalError, ProposalPaths, load_proposal, set_state, write_private


@dataclass(frozen=True, slots=True)
class Assembly:
    """The assembled private document with missing-section reminders."""

    path: Path
    document: str
    missing_sections: tuple[str, ...]
    reminder: str


def _evidence_sources(proposal: Any) -> str:
    sidecars = tuple(
        path for section in proposal.sections
        if (path := section.path.with_suffix(".evidence.json")).is_file()
    )
    if not sidecars:
        return ""
    testing = proposal_knowledge.module("automation.knowledge.testing")
    facade = proposal_knowledge.module("automation.knowledge.facade")
    rendering = proposal_knowledge.module("automation.knowledge.render")
    packs = []
    for sidecar in sidecars:
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
        packs.append(testing.pack_from_dict(raw))
    if not packs:
        return ""
    unique: dict[str, Any] = {}
    for pack in packs:
        for item in pack.items:
            unique.setdefault(item.sha256, item)
    items = tuple(replace(item, id=f"E{index}") for index, item in enumerate(unique.values(), start=1))
    layers = {
        name: next((pack.layers[name] for pack in packs if pack.layers.get(name) == "hit"), packs[-1].layers.get(name, "skipped"))
        for name in ("rag", "wiki", "twin")
    }
    verdict = "hit" if items else "unavailable" if any(pack.verdict == "unavailable" for pack in packs) else "no_evidence"
    combined = facade.EvidencePack("knowledge-v1", packs[0].query, verdict, items, layers)
    return str(rendering.render_citations(combined, "sources"))


def assemble(paths: ProposalPaths, slug: str) -> Assembly:
    """Assemble all sections and append one deduplicated facade-rendered source list."""
    proposal = load_proposal(paths, slug)
    missing: list[str] = []
    sections: list[str] = [f"# {proposal.title}"]
    for section in proposal.sections:
        body = section.body.strip()
        if section.path.with_suffix(".evidence.json").is_file():
            body = body.partition("\n\n### 근거\n\n")[0].rstrip()
        if not body:
            missing.append(section.key)
            body = f"[MISSING SECTION: {section.title}]"
        sections.append(f"## {section.title}\n\n{body}")
    sources = _evidence_sources(proposal)
    if sources:
        sections.append(f"## 근거 목록\n\n{sources}")
    document = "\n\n".join(sections).rstrip() + "\n"
    path = proposal.workspace / "assembled.md"
    write_private(path, document)
    state = "assembled" if not missing else "needs-drafts"
    _ = set_state(paths, slug, state)
    reminder = "" if not missing else "Missing section drafts: " + ", ".join(
        section.title for section in proposal.sections if section.key in missing
    )
    return Assembly(path, document, tuple(missing), reminder)


def append_final_review(paths: ProposalPaths, slug: str, review: str) -> Path:
    """Record a single Codex review in the assembled private document."""
    proposal = load_proposal(paths, slug)
    if proposal.state == "reviewed":
        raise ProposalError("final review already recorded")
    path = proposal.workspace / "assembled.md"
    if not path.is_file() or not review.strip():
        raise ProposalError("assembled proposal and review are required")
    document = path.read_text(encoding="utf-8").rstrip()
    write_private(path, f"{document}\n\n## Final review comments\n\n{review.strip()}\n")
    _ = set_state(paths, slug, "reviewed")
    return path
