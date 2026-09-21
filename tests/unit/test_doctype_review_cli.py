"""Document locators reach the owner-notice facade from both CLI review paths."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from automation import owner_notice
from skills.doctype.scripts import doctype_cli
from skills.proposal.scripts import proposal_assembly, proposal_cli
from tests.unit.test_doctype_skill import _prepared_cli
from tests.unit.test_proposal_skill import _paths, _ready_proposal


@pytest.fixture
def delivered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """ON-1..ON-3: 두 CLI 모두 목적지 해석과 전송을 파사드에 맡긴다."""
    sent: list[str] = []
    monkeypatch.setenv("DOCTYPE_DM_TARGET", "discord:111")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "cli-token")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "notice-1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    monkeypatch.setattr(owner_notice, "send_notice", lambda _token, _channel, body: sent.append(body))
    return sent


def test_doctype_review_carries_document_location_when_drafted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delivered: list[str],
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
    assert rc == 0
    assert len(delivered) == 1
    content = delivered[0]
    assert file.name in content.splitlines()[2]
    assert "https://discord.com/" not in content
    assert len(content.splitlines()) == 5


def test_proposal_review_carries_document_location_when_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delivered: list[str],
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
    assert rc == 0
    assert len(delivered) == 1
    content = delivered[0]
    assert assembled.path.name in content.splitlines()[2]
    assert "https://discord.com/" not in content
    assert len(content.splitlines()) == 5
