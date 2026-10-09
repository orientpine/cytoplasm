from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "skills" / "patent-prep"
sys.path.insert(0, str(SKILL_ROOT))

patent_core = import_module("scripts.patent_core")
patent_llm = import_module("scripts.patent_llm")
patent_routing = import_module("scripts.patent_routing")
patent_storage = import_module("scripts.patent_storage")


@dataclass(frozen=True, slots=True)
class InvocationResult:
    returncode: int
    stdout: str


def _paths(tmp_path: Path):
    return patent_storage.PatentPaths(tmp_path / "agent" / "patent-drafts", tmp_path / "repo-progress")


def test_disclosure_form_assembly_creates_private_templates(tmp_path: Path) -> None:
    # Given
    paths = _paths(tmp_path)

    # When
    progress = patent_core.create_disclosure(paths, "sample-disclosure")

    # Then
    root = paths.workspace_root / "sample-disclosure"
    assert progress.percent_complete == 0
    assert (root.stat().st_mode & 0o777) == 0o700
    assert "Technical approach" in (root / "disclosure-form.md").read_text(encoding="utf-8")
    assert "Search patent databases" in (root / "prior-art-checklist.md").read_text(encoding="utf-8")


def test_prior_art_checklist_updates_progress_without_body(tmp_path: Path) -> None:
    # Given
    paths = _paths(tmp_path)
    _ = patent_core.create_disclosure(paths, "sample-disclosure")

    # When
    progress = patent_core.update_checklist(paths, "sample-disclosure", patent_storage.ChecklistState.COMPLETE)

    # Then
    assert progress.checklist_state is patent_storage.ChecklistState.COMPLETE
    assert progress.percent_complete == 50


def test_draft_dispatch_does_not_add_content_tags() -> None:
    # Given
    commands: list[tuple[str, ...]] = []

    def invoke(command: tuple[str, ...]) -> InvocationResult:
        commands.append(command)
        return InvocationResult(0, "Synthetic draft.")

    # When
    response = patent_llm.generate_draft("private material", (), invoke)

    # Then
    assert response.call.tags == ()
    assert len(commands) == 1
    assert commands[0][:5] == ("hermes", "-z", "private material", "-t", "todo")
    assert commands[0][5] == "--usage-file" and len(commands[0]) == 7


def test_patent_call_preserves_only_requested_tags() -> None:
    # Given
    requested_tags = ("review",)

    # When
    call = patent_routing.plan_patent_call(requested_tags)

    # Then
    assert call.provider == patent_routing.CODEX_PROVIDER
    assert call.model == patent_routing.CODEX_MODEL
    assert call.tags == ("review",)


def test_progress_metadata_never_contains_private_draft_body(tmp_path: Path) -> None:
    # Given
    paths = _paths(tmp_path)
    _ = patent_core.create_disclosure(paths, "sample-disclosure")
    brief = paths.workspace_root / "sample-disclosure" / "brief.md"
    brief.write_text("private brief marker", encoding="utf-8")

    # When
    def invoke(_: tuple[str, ...]) -> InvocationResult:
        return InvocationResult(0, "private draft marker")

    result = patent_core.draft_disclosure(
        paths,
        "sample-disclosure",
        brief,
        invoke=invoke,
    )

    # Then
    metadata = (paths.status_root / "sample-disclosure.json").read_text(encoding="utf-8")
    assert result.path.is_file()
    assert "private draft marker" not in metadata
    assert '"slug": "sample-disclosure"' in metadata
