"""Document locators reach the real Hermes subprocess from both CLI review paths."""
from __future__ import annotations

import os
import shlex
from pathlib import Path

import pytest

from skills.doctype.scripts import doctype_cli
from skills.proposal.scripts import proposal_assembly, proposal_cli
from tests.unit.test_doctype_skill import _prepared_cli
from tests.unit.test_proposal_skill import _paths, _ready_proposal


@pytest.fixture
def delivered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The real subprocess executes this isolated transport recorder, never Hermes/network.
    output = tmp_path / "delivered.txt"
    binary = tmp_path / "hermes"
    binary.write_text(f"#!/bin/sh\nprintf '%s' \"$4\" >> {shlex.quote(str(output))}\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("DOCTYPE_DM_HERMES_BIN", str(binary))
    monkeypatch.setenv("DOCTYPE_DM_TARGET", "discord:111")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    return output


def test_doctype_review_carries_document_location_when_drafted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delivered: Path,
) -> None:
    # Given a real registry, replay LLM and artifact generation.
    _, _, inputs = _prepared_cli(tmp_path, monkeypatch)
    file = tmp_path / "drafts" / "draft with spaces.md"
    # When
    rc = doctype_cli.main([
        "draft", "--name", "업체추천사유서", "--inputs-json", str(inputs),
        "--out", str(file), "--review",
    ])
    # Then: the locator line must carry the actual document, not a guessed channel link.
    content = delivered.read_text(encoding="utf-8")
    assert rc == 0
    assert file.name in content.splitlines()[2]
    assert "https://discord.com/" not in content
    assert len(content.splitlines()) == 5


def test_proposal_review_carries_document_location_when_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delivered: Path,
) -> None:
    # Given a real assembled proposal and recorded review response.
    paths = _paths(tmp_path)
    _ready_proposal(paths)
    assembled = proposal_assembly.assemble(paths, "renewal-plan")
    response = tmp_path / "response.txt"
    response.write_text("검토 의견", encoding="utf-8")
    monkeypatch.setattr(proposal_cli, "_paths", lambda: paths)
    # When
    rc = proposal_cli.main([
        "review", "--slug", "renewal-plan", "--dm-target", "discord:111",
        "--response-file", str(response),
    ])
    # Then
    content = delivered.read_text(encoding="utf-8")
    assert rc == 0
    assert assembled.path.name in content.splitlines()[2]
    assert "https://discord.com/" not in content
    assert len(content.splitlines()) == 5
