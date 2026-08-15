"""Private patent workspace and content-free progress metadata."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final


_SLUG: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


class PatentStorageError(RuntimeError):
    """A private workspace or progress metadata contract was violated."""


class ChecklistState(StrEnum):
    """The body-free review progress states."""

    NOT_STARTED = "not-started"
    IN_PROGRESS = "in-progress"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class PatentPaths:
    """Explicit roots keep private material and test metadata isolated."""

    workspace_root: Path
    status_root: Path

    @classmethod
    def from_environment(cls) -> PatentPaths:
        """Build protected roots without placing invention content in the repository."""
        return cls(
            Path(os.environ.get("PATENT_DRAFT_ROOT", "~/patent-drafts")).expanduser(),
            Path(os.environ.get("PATENT_STATUS_ROOT", "~/.hermes/patent-status")).expanduser(),
        )


@dataclass(frozen=True, slots=True)
class Progress:
    """The sole projection permitted outside the private workspace."""

    slug: str
    checklist_state: ChecklistState
    percent_complete: int


def private_directory(path: Path) -> None:
    """Create or repair a mode-700 private directory."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_private(path: Path, content: str) -> None:
    """Write one protected mode-600 private file."""
    private_directory(path.parent)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def require_slug(slug: str) -> str:
    """Reject unsafe workspace path components."""
    if _SLUG.fullmatch(slug) is None:
        raise PatentStorageError("invalid patent workspace slug")
    return slug


def workspace(paths: PatentPaths, slug: str) -> Path:
    """Resolve the private workspace for one validated slug."""
    return paths.workspace_root / require_slug(slug)


def status_path(paths: PatentPaths, slug: str) -> Path:
    """Resolve the content-free progress record for one validated slug."""
    return paths.status_root / f"{require_slug(slug)}.json"


def initialize(paths: PatentPaths, slug: str, form: str, checklist: str) -> Progress:
    """Create the protected form/checklist workspace and initial progress record."""
    root = workspace(paths, slug)
    if root.exists():
        raise PatentStorageError("patent workspace already exists")
    private_directory(root)
    write_private(root / "disclosure-form.md", form)
    write_private(root / "prior-art-checklist.md", checklist)
    progress = Progress(require_slug(slug), ChecklistState.NOT_STARTED, 0)
    write_progress(paths, progress)
    return progress


def write_draft(paths: PatentPaths, slug: str, body: str) -> Progress:
    """Store a non-empty draft only in the protected workspace."""
    if not body.strip():
        raise PatentStorageError("patent draft is empty")
    root = workspace(paths, slug)
    if not root.is_dir():
        raise PatentStorageError("patent workspace does not exist")
    write_private(root / "draft.md", body.strip() + "\n")
    prior = load_progress(paths, slug)
    progress = Progress(prior.slug, prior.checklist_state, _completion(prior.checklist_state, True))
    write_progress(paths, progress)
    return progress


def set_checklist_state(paths: PatentPaths, slug: str, state: ChecklistState) -> Progress:
    """Update only checklist state and derived completion percentage."""
    root = workspace(paths, slug)
    if not root.is_dir():
        raise PatentStorageError("patent workspace does not exist")
    progress = Progress(require_slug(slug), state, _completion(state, (root / "draft.md").is_file()))
    write_progress(paths, progress)
    return progress


def write_progress(paths: PatentPaths, progress: Progress) -> None:
    """Persist the intentionally minimal, body-free progress projection."""
    private_directory(paths.status_root)
    payload = {
        "slug": progress.slug,
        "checklist_state": progress.checklist_state.value,
        "percent_complete": progress.percent_complete,
    }
    write_private(status_path(paths, progress.slug), json.dumps(payload, sort_keys=True) + "\n")


def load_progress(paths: PatentPaths, slug: str) -> Progress:
    """Load and structurally validate content-free progress metadata."""
    path = status_path(paths, slug)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PatentStorageError("patent progress does not exist") from error
    except json.JSONDecodeError as error:
        raise PatentStorageError("patent progress is invalid") from error
    if not isinstance(payload, dict):
        raise PatentStorageError("patent progress is invalid")
    raw_slug = payload.get("slug")
    raw_state = payload.get("checklist_state")
    raw_percent = payload.get("percent_complete")
    if not isinstance(raw_slug, str) or not isinstance(raw_state, str) or not isinstance(raw_percent, int):
        raise PatentStorageError("patent progress is invalid")
    try:
        state = ChecklistState(raw_state)
    except ValueError as error:
        raise PatentStorageError("patent progress is invalid") from error
    if raw_percent not in (0, 25, 50, 75, 100):
        raise PatentStorageError("patent progress is invalid")
    return Progress(require_slug(raw_slug), state, raw_percent)


def _completion(state: ChecklistState, drafted: bool) -> int:
    base = {
        ChecklistState.NOT_STARTED: 0,
        ChecklistState.IN_PROGRESS: 25,
        ChecklistState.COMPLETE: 50,
    }[state]
    return base + (50 if drafted else 0)
