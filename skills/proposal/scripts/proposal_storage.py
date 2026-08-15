"""Private proposal metadata storage and mode enforcement."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final


_SLUG: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


class ProposalError(RuntimeError):
    """A proposal workspace contract was violated."""


class SectionState(StrEnum):
    """The body-derived state mirrored into status metadata."""

    PLANNED = "planned"
    DRAFTED = "drafted"


@dataclass(frozen=True, slots=True)
class ProposalPaths:
    """Explicit path roots keep tests and production state isolated."""

    workspace_root: Path
    status_root: Path
    rules_file: Path

    @classmethod
    def from_environment(cls) -> ProposalPaths:
        skill_root = Path(__file__).resolve().parents[1]
        return cls(
            workspace_root=Path(os.environ.get("PROPOSAL_WORKSPACE_ROOT", "~/proposals")).expanduser(),
            status_root=Path(
                os.environ.get("PROPOSAL_STATUS_ROOT", "~/.hermes/proposal-status")
            ).expanduser(),
            rules_file=Path(
                os.environ.get("PROPOSAL_RULES_PATH", str(skill_root / "configs/sensitivity-rules.yaml"))
            ).expanduser(),
        )


@dataclass(frozen=True, slots=True)
class Section:
    """Section body storage is constrained to the proposal workspace."""

    key: str
    title: str
    state: SectionState
    card_id: str
    path: Path
    body: str


@dataclass(frozen=True, slots=True)
class Proposal:
    """Metadata-only view; it deliberately excludes proposal body text."""

    slug: str
    title: str
    state: str
    workspace: Path
    status_path: Path
    sections: tuple[Section, ...]


def private_directory(path: Path) -> None:
    """Create a mode-700 directory used for private proposal material."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_private(path: Path, content: str) -> None:
    """Write a mode-600 file under a protected parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def require_slug(slug: str) -> str:
    """Reject paths outside the stable proposal slug namespace."""
    if _SLUG.fullmatch(slug) is None:
        raise ProposalError("invalid proposal slug")
    return slug


def valid_section_key(key: str) -> bool:
    """Return whether a user section key is a safe workspace component."""
    return _SLUG.fullmatch(key) is not None


def workspace(paths: ProposalPaths, slug: str) -> Path:
    """Return the protected workspace path for one validated slug."""
    return paths.workspace_root / require_slug(slug)


def section_path(workspace_root: Path, index: int, key: str) -> Path:
    """Render a stable, ordered filename without exposing body content."""
    return workspace_root / "sections" / f"{index:02d}-{key}.md"


def _manifest_path(paths: ProposalPaths, slug: str) -> Path:
    return workspace(paths, slug) / "manifest.json"


def _status_path(paths: ProposalPaths, slug: str) -> Path:
    return paths.status_root / f"{require_slug(slug)}.json"


def load_manifest(paths: ProposalPaths, slug: str) -> dict[str, object]:
    """Load the private body-free manifest with strict structural checks."""
    path = _manifest_path(paths, slug)
    if not path.is_file():
        raise ProposalError("proposal does not exist")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ProposalError("proposal manifest is invalid") from error
    if not isinstance(payload, dict):
        raise ProposalError("proposal manifest is invalid")
    return payload


def write_manifest(paths: ProposalPaths, slug: str, manifest: dict[str, object]) -> None:
    """Persist private workspace metadata while retaining mode 600."""
    write_private(_manifest_path(paths, slug), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def _sections(paths: ProposalPaths, slug: str, manifest: dict[str, object]) -> tuple[Section, ...]:
    records = manifest.get("sections")
    if not isinstance(records, list):
        raise ProposalError("proposal sections are invalid")
    root = workspace(paths, slug)
    sections: list[Section] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ProposalError("proposal section is invalid")
        key_value, title_value, state_value, card_id_value = (
            record.get("key"),
            record.get("title"),
            record.get("state"),
            record.get("card_id", ""),
        )
        if (
            not isinstance(key_value, str)
            or not isinstance(title_value, str)
            or not isinstance(state_value, str)
            or not isinstance(card_id_value, str)
        ):
            raise ProposalError("proposal section is invalid")
        key, title, state, card_id = (
            str(key_value),
            str(title_value),
            str(state_value),
            str(card_id_value),
        )
        try:
            section_state = SectionState(state)
        except ValueError as error:
            raise ProposalError("proposal section state is invalid") from error
        path = section_path(root, index, key)
        body = path.read_text(encoding="utf-8") if path.is_file() else ""
        sections.append(Section(key, title, section_state, card_id, path, body))
    return tuple(sections)


def load_proposal(paths: ProposalPaths, slug: str) -> Proposal:
    """Build the only supported metadata projection from the manifest."""
    manifest = load_manifest(paths, slug)
    title, state = manifest.get("title"), manifest.get("state")
    if not isinstance(title, str) or not isinstance(state, str):
        raise ProposalError("proposal manifest is invalid")
    return Proposal(slug, title, state, workspace(paths, slug), _status_path(paths, slug), _sections(paths, slug, manifest))


def sync_status(paths: ProposalPaths, proposal: Proposal) -> None:
    """Write status metadata only; proposal drafts and reviews never enter it."""
    private_directory(paths.status_root)
    payload = {
        "slug": proposal.slug,
        "sections": [
            {"key": item.key, "title": item.title, "state": item.state.value, "card_id": item.card_id}
            for item in proposal.sections
        ],
        "state": proposal.state,
    }
    write_private(proposal.status_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def update_section(paths: ProposalPaths, slug: str, key: str, section: Section) -> Proposal:
    """Update one metadata record after its private body operation succeeds."""
    manifest = load_manifest(paths, slug)
    records = manifest["sections"]
    if not isinstance(records, list):
        raise ProposalError("proposal sections are invalid")
    for record in records:
        if isinstance(record, dict) and record.get("key") == key:
            record["state"] = section.state.value
            record["card_id"] = section.card_id
            write_manifest(paths, slug, manifest)
            proposal = load_proposal(paths, slug)
            sync_status(paths, proposal)
            return proposal
    raise ProposalError("section does not exist")


def set_state(paths: ProposalPaths, slug: str, state: str) -> Proposal:
    """Advance a body-free proposal lifecycle state in both metadata files."""
    manifest = load_manifest(paths, slug)
    manifest["state"] = state
    write_manifest(paths, slug, manifest)
    proposal = load_proposal(paths, slug)
    sync_status(paths, proposal)
    return proposal
