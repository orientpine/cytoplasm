"""Proposal lifecycle actions that keep all body material off repository paths."""

from __future__ import annotations

from dataclasses import replace

from .proposal_storage import (
    Proposal,
    ProposalError,
    ProposalPaths,
    Section,
    SectionState,
    load_manifest,
    load_proposal,
    private_directory,
    require_slug,
    sync_status,
    update_section,
    valid_section_key,
    workspace,
    write_manifest,
    write_private,
)


def create_proposal(
    paths: ProposalPaths, slug: str, title: str, sections: tuple[tuple[str, str], ...]
) -> Proposal:
    """Create a 700 workspace plus status metadata that contains no body fields."""
    slug = require_slug(slug)
    if not title.strip() or not sections:
        raise ProposalError("proposal requires a title and at least one section")
    keys = tuple(key for key, _ in sections)
    if len(set(keys)) != len(keys) or any(not valid_section_key(key) for key in keys):
        raise ProposalError("section keys must be unique kebab-case identifiers")
    proposal_workspace = workspace(paths, slug)
    if proposal_workspace.exists():
        raise ProposalError("proposal already exists")
    private_directory(proposal_workspace)
    private_directory(proposal_workspace / "sections")
    manifest: dict[str, object] = {
        "slug": slug,
        "title": title.strip(),
        "state": "drafting",
        "sections": [
            {"key": key, "title": section_title.strip(), "state": SectionState.PLANNED.value, "card_id": ""}
            for key, section_title in sections
        ],
    }
    write_manifest(paths, slug, manifest)
    proposal = load_proposal(paths, slug)
    sync_status(paths, proposal)
    return proposal


def add_section(paths: ProposalPaths, slug: str, key: str, title: str) -> Proposal:
    """Add a planned section without creating any proposal body content."""
    proposal = load_proposal(paths, slug)
    if not valid_section_key(key) or not title.strip() or any(item.key == key for item in proposal.sections):
        raise ProposalError("invalid or duplicate section")
    manifest = load_manifest(paths, slug)
    records = manifest["sections"]
    if not isinstance(records, list):
        raise ProposalError("proposal sections are invalid")
    records.append({"key": key, "title": title.strip(), "state": SectionState.PLANNED.value, "card_id": ""})
    write_manifest(paths, slug, manifest)
    proposal = load_proposal(paths, slug)
    sync_status(paths, proposal)
    return proposal


def list_sections(paths: ProposalPaths, slug: str) -> tuple[Section, ...]:
    """List the ordered local sections for the proposal workspace."""
    return load_proposal(paths, slug).sections


def read_section(paths: ProposalPaths, slug: str, key: str) -> Section:
    """Resolve a section by stable key before any file mutation."""
    for section in load_proposal(paths, slug).sections:
        if section.key == key:
            return section
    raise ProposalError("section does not exist")


def bind_card(paths: ProposalPaths, slug: str, key: str, card_id: str) -> Proposal:
    """Record only the real Hermes Kanban id in status metadata."""
    section = read_section(paths, slug, key)
    if not card_id:
        raise ProposalError("kanban card id is missing")
    return update_section(paths, slug, key, replace(section, card_id=card_id))


def write_draft(paths: ProposalPaths, slug: str, key: str, body: str) -> Proposal:
    """Store a non-empty draft only in the protected proposal workspace."""
    section = read_section(paths, slug, key)
    if not body.strip():
        raise ProposalError("section draft is empty")
    write_private(section.path, body.strip() + "\n")
    return update_section(paths, slug, key, replace(section, state=SectionState.DRAFTED, body=body.strip()))


def fold_contribution(paths: ProposalPaths, slug: str, key: str, content: str, source: str) -> Proposal:
    """Fold human-delivered material into a section; fetching is intentionally absent."""
    section = read_section(paths, slug, key)
    if not content.strip() or not source.strip():
        raise ProposalError("contribution content and source are required")
    prefix = section.body.strip()
    folded = f"{prefix}\n\n" if prefix else ""
    folded += f"### Human-provided material ({source.strip()})\n\n{content.strip()}"
    return write_draft(paths, slug, key, folded)


def proposal_text(paths: ProposalPaths, slug: str) -> str:
    """Return private material for deterministic sensitivity routing only."""
    proposal = load_proposal(paths, slug)
    return "\n\n".join(f"{section.title}\n{section.body}" for section in proposal.sections)
