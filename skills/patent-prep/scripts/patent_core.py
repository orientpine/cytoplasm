"""Patent-prep workflow actions with private-only content persistence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .patent_forms import disclosure_form, prior_art_checklist
from .patent_llm import DraftResponse, Invoke, generate_draft
from .patent_storage import ChecklistState, PatentPaths, Progress, initialize, set_checklist_state, workspace, write_draft


class PatentWorkflowError(RuntimeError):
    """A patent-prep workflow boundary was violated."""


@dataclass(frozen=True, slots=True)
class DraftedDisclosure:
    """The private output path plus safe routing and progress facts."""

    path: Path
    response: DraftResponse
    progress: Progress


def create_disclosure(paths: PatentPaths, slug: str) -> Progress:
    """Create a blank private form and checklist without recording invention data."""
    return initialize(paths, slug, disclosure_form(), prior_art_checklist())


def update_checklist(paths: PatentPaths, slug: str, state: ChecklistState) -> Progress:
    """Record only body-free prior-art checklist progress."""
    return set_checklist_state(paths, slug, state)


def draft_disclosure(
    paths: PatentPaths,
    slug: str,
    brief_path: Path,
    requested_tags: tuple[str, ...] = (),
    invoke: Invoke | None = None,
) -> DraftedDisclosure:
    """Generate and store one draft from a workspace-confined private brief."""
    root = workspace(paths, slug).resolve()
    source = brief_path.expanduser().resolve()
    if not source.is_file() or not source.is_relative_to(root):
        raise PatentWorkflowError("brief must be a file inside the private patent workspace")
    brief = source.read_text(encoding="utf-8")
    if not brief.strip():
        raise PatentWorkflowError("brief is empty")
    prompt = (
        "Draft a concise Korean invention-disclosure narrative using only the supplied private material. "
        "Do not invent facts, do not use tools, and return only the draft.\n\nPRIVATE MATERIAL:\n"
        f"{brief.strip()}"
    )
    response = generate_draft(prompt, requested_tags, invoke)
    progress = write_draft(paths, slug, response.text)
    return DraftedDisclosure(root / "draft.md", response, progress)
