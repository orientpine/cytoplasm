"""Assembly and final-review persistence for protected proposal workspaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .proposal_storage import ProposalError, ProposalPaths, load_proposal, set_state, write_private


@dataclass(frozen=True, slots=True)
class Assembly:
    """The assembled private document with missing-section reminders."""

    path: Path
    document: str
    missing_sections: tuple[str, ...]
    reminder: str


def assemble(paths: ProposalPaths, slug: str) -> Assembly:
    """Assemble all sections and mark missing drafts without a failure exit."""
    proposal = load_proposal(paths, slug)
    missing: list[str] = []
    sections: list[str] = [f"# {proposal.title}"]
    for section in proposal.sections:
        body = section.body.strip()
        if not body:
            missing.append(section.key)
            body = f"[MISSING SECTION: {section.title}]"
        sections.append(f"## {section.title}\n\n{body}")
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
