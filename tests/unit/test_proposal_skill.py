from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from skills.proposal.scripts import proposal_assembly, proposal_core, proposal_llm, proposal_sensitivity  # noqa: E402
from skills.proposal.scripts.proposal_storage import ProposalPaths, SectionState  # noqa: E402


RULES = ROOT / "configs" / "sensitivity-rules.yaml"


def _paths(tmp_path: Path) -> ProposalPaths:
    return ProposalPaths(
        workspace_root=tmp_path / "agent" / "proposals",
        status_root=tmp_path / "repo-status",
        rules_file=RULES,
    )


def test_create_workspace_records_section_metadata_without_body(tmp_path: Path) -> None:
    # Given
    paths = _paths(tmp_path)

    # When
    proposal = proposal_core.create_proposal(
        paths,
        "renewal-plan",
        "Renewal plan",
        (("need", "Need"), ("approach", "Approach")),
    )

    # Then
    assert proposal.workspace.is_dir()
    assert (proposal.workspace.stat().st_mode & 0o777) == 0o700
    assert [section.key for section in proposal_core.list_sections(paths, "renewal-plan")] == [
        "need",
        "approach",
    ]
    assert proposal.status_path.read_text(encoding="utf-8")


def test_add_and_list_sections_preserves_order(tmp_path: Path) -> None:
    # Given
    paths = _paths(tmp_path)
    _ = proposal_core.create_proposal(paths, "renewal-plan", "Renewal plan", (("need", "Need"),))

    # When
    _ = proposal_core.add_section(paths, "renewal-plan", "approach", "Approach")

    # Then
    assert [section.key for section in proposal_core.list_sections(paths, "renewal-plan")] == [
        "need",
        "approach",
    ]


def test_section_draft_and_human_contribution_are_folded_into_section(tmp_path: Path) -> None:
    # Given
    paths = _paths(tmp_path)
    _ = proposal_core.create_proposal(paths, "renewal-plan", "Renewal plan", (("need", "Need"),))

    # When
    _ = proposal_core.write_draft(paths, "renewal-plan", "need", "Initial rationale.")
    _ = proposal_core.fold_contribution(paths, "renewal-plan", "need", "Human supplied evidence.", "partner")

    # Then
    section = proposal_core.read_section(paths, "renewal-plan", "need")
    assert "Initial rationale." in section.body
    assert "Human supplied evidence." in section.body
    assert section.state == SectionState.DRAFTED


def test_assembly_contains_every_completed_section(tmp_path: Path) -> None:
    # Given
    paths = _paths(tmp_path)
    _ = proposal_core.create_proposal(
        paths,
        "renewal-plan",
        "Renewal plan",
        (("need", "Need"), ("approach", "Approach")),
    )
    _ = proposal_core.write_draft(paths, "renewal-plan", "need", "Need draft.")
    _ = proposal_core.write_draft(paths, "renewal-plan", "approach", "Approach draft.")

    # When
    assembled = proposal_assembly.assemble(paths, "renewal-plan")

    # Then
    assert assembled.missing_sections == ()
    assert "## Need" in assembled.document
    assert "## Approach" in assembled.document
    assert assembled.path.is_file()
    assert (assembled.path.stat().st_mode & 0o777) == 0o600


def test_assembly_marks_missing_sections_and_returns_reminder(tmp_path: Path) -> None:
    # Given
    paths = _paths(tmp_path)
    _ = proposal_core.create_proposal(
        paths,
        "renewal-plan",
        "Renewal plan",
        (("need", "Need"), ("approach", "Approach")),
    )
    _ = proposal_core.write_draft(paths, "renewal-plan", "need", "Need draft.")

    # When
    assembled = proposal_assembly.assemble(paths, "renewal-plan")

    # Then
    assert assembled.missing_sections == ("approach",)
    assert "[MISSING SECTION: Approach]" in assembled.document
    assert "Approach" in assembled.reminder


def test_final_review_invokes_codex_once_with_required_model() -> None:
    # Given
    commands: list[tuple[str, ...]] = []

    def invoke(command: tuple[str, ...]) -> proposal_llm.InvocationResult:
        commands.append(command)
        return proposal_llm.InvocationResult(returncode=0, stdout="Review comments.")

    # When
    review = proposal_llm.run_final_review("# Proposal", invoke)

    # Then
    assert review == "Review comments."
    assert commands == [
        (
            "hermes",
            "-z",
            "# Proposal",
            "--provider",
            "openai-codex",
            "-m",
            "gpt-5.4",
            "-t",
            "todo",
        )
    ]


def test_sensitive_proposal_routes_drafting_off_glm(tmp_path: Path) -> None:
    # Given
    paths = _paths(tmp_path)
    _ = proposal_core.create_proposal(paths, "renewal-plan", "Renewal plan", (("need", "Need"),))
    _ = proposal_core.write_draft(paths, "renewal-plan", "need", "Patent filing material.")

    # When
    route = proposal_sensitivity.route_proposal(
        proposal_core.proposal_text(paths, "renewal-plan"), proposal_sensitivity.load_rules(paths.rules_file)
    )

    # Then
    assert route.sensitive is True
    assert route.provider == "openai-codex"
    assert route.model == "gpt-5.4"


def test_status_metadata_never_contains_draft_or_contribution_body(tmp_path: Path) -> None:
    # Given
    paths = _paths(tmp_path)
    proposal = proposal_core.create_proposal(paths, "renewal-plan", "Renewal plan", (("need", "Need"),))
    _ = proposal_core.write_draft(paths, "renewal-plan", "need", "Private body alpha.")
    _ = proposal_core.fold_contribution(paths, "renewal-plan", "need", "Private body beta.", "human")

    # When
    metadata = proposal.status_path.read_text(encoding="utf-8")

    # Then
    assert "Private body alpha." not in metadata
    assert "Private body beta." not in metadata
    assert '"slug": "renewal-plan"' in metadata
    assert '"state": "drafted"' in metadata


def test_glm_hermes_child_receives_key_from_secrets_when_parent_environment_lacks_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    binary = tmp_path / ".local" / "bin" / "hermes"
    binary.parent.mkdir(parents=True)
    _ = binary.write_text(
        '#!/bin/sh\n[ -n "$LITELLM_AGENT_KEY" ] || exit 9\nprintf "draft"\n',
        encoding="utf-8",
    )
    _ = binary.chmod(0o755)
    _ = (tmp_path / ".env.secrets").write_text(
        "LITELLM_AGENT_KEY=proposal-fallback-key\n", encoding="utf-8"
    )
    monkeypatch.delenv("LITELLM_AGENT_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PROPOSAL_LLM_LOG_ROOT", str(tmp_path / "logs"))

    # When
    result = proposal_llm.run_section_draft(
        "prompt", "custom:litellm", "glm-main", False
    )

    # Then
    assert result == "draft"
