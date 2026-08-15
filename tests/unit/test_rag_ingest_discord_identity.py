from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest

from automation.interop.report import ReportStatus, TaskReport, format_report
from automation.rag_ingest import queuefile
from automation.rag_ingest.config import DiscordSourceConfig, IngestConfig
from automation.rag_ingest.mcp_client import JsonValue, McpMemoryClient
from automation.rag_ingest.pipeline import run_pipeline
from automation.rag_ingest.sources import discord_team


_SELF_BOT_ID = "100000000000000010"
_PEER_BOT_ID = "100000000000000002"
_OTHER_BOT_ID = "100000000000000003"
_UNKNOWN_BOT_ID = "100000000000000099"
_PUBLIC_KEY = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g"
)


class RecordingMcpClient(McpMemoryClient):
    def __init__(self) -> None:
        super().__init__(base_url="http://fake.invalid", api_key="test-key")
        self.loaded: list[tuple[str, str, dict[str, str]]] = []

    @override
    def load_memory(
        self,
        content: str,
        source: str,
        metadata: dict[str, str],
    ) -> dict[str, JsonValue]:
        self.loaded.append((content, source, metadata))
        return {"document_id": f"point-{len(self.loaded)}"}

    @override
    def delete_memory(self, document_id: str) -> dict[str, JsonValue]:
        return {"deleted": True, "document_id": document_id}


def _config(tmp_path: Path) -> IngestConfig:
    return IngestConfig(
        mcp_base_url="http://fake.invalid",
        api_key="test-key",
        state_dir=tmp_path / "state",
        wiki_dir=tmp_path / "wiki",
        notes_dir=tmp_path / "notes",
        meetings_dir=tmp_path / "meetings",
        hermes_db=None,
        perspective={
            "agent_id": "local-agent",
            "owner": "test-owner",
            "role": "research-agent",
            "project": "identity-binding",
            "interest_tags": "security",
        },
        discord=DiscordSourceConfig(
            enabled=True,
            guild_id="guild-test",
            team_channel="team",
            agents_log_channel="agents-log",
            token_env="DISCORD_BOT_TOKEN",
        ),
        discord_token="test-token",
    )


def _write_roster(path: Path) -> None:
    roster = "\n".join(
        (
            "schema: 1",
            "group_id: test-group",
            "admin:",
            "  name: Test Admin",
            '  discord_user_id: "100000000000000001"',
            "  publisher_principal: publisher-test-admin@autophagy",
            f"  signing_public_key: {_PUBLIC_KEY}",
            "members:",
            "  - name: Peer",
            f'    discord_user_id: "{_PEER_BOT_ID}"',
            "    node_label: peer-test",
            "    status: active",
            "  - name: Other",
            f'    discord_user_id: "{_OTHER_BOT_ID}"',
            "    node_label: peer-other",
            "    status: active",
        )
    )
    _ = path.write_text(f"{roster}\n", encoding="utf-8")


def _report(agent_id: str, marker: str) -> str:
    return format_report(
        TaskReport(
            agent_id=agent_id,
            task_id="W-F2.5-B",
            status=ReportStatus.DONE,
            summary=marker,
            links=(),
            timestamp=datetime(2026, 8, 15, 1, 2, tzinfo=UTC),
        )
    )


def _install_discord_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    author_id: str,
    claimed_agent_id: str,
    marker: str,
) -> None:
    message: JsonValue = {
        "id": "300000000000000001",
        "author": {"id": author_id, "username": "fixture-bot"},
        "content": _report(claimed_agent_id, marker),
    }

    def request(path: str, token: str, network_log: list[str]) -> JsonValue:
        del token
        network_log.append(path)
        if path == "/guilds/guild-test/channels":
            return [
                {"id": "channel-team", "name": "team"},
                {"id": "channel-reports", "name": "agents-log"},
            ]
        if path == "/users/@me":
            return {"id": _SELF_BOT_ID}
        if path.startswith("/channels/channel-reports/messages?"):
            return [message]
        if path.startswith("/channels/channel-team/messages?"):
            return []
        raise AssertionError(path)

    monkeypatch.setattr(discord_team, "_request", request)


def test_discord_report_when_author_matches_roster_principal_then_reaches_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    roster_path = tmp_path / "roster.yaml"
    _write_roster(roster_path)
    monkeypatch.setenv("AUTOPHAGY_ROSTER", str(roster_path))
    _install_discord_fixture(
        monkeypatch,
        author_id=_PEER_BOT_ID,
        claimed_agent_id="peer-test",
        marker="LEGITIMATE_REPORT_MARKER",
    )
    client = RecordingMcpClient()

    # When
    pending, _log_lines = run_pipeline(_config(tmp_path), {"discord"}, client=client)

    # Then
    assert pending == 0
    assert len(client.loaded) == 1
    content, _source, metadata = client.loaded[0]
    assert "LEGITIMATE_REPORT_MARKER" in content
    assert metadata["report_agent_id"] == "peer-test"


@pytest.mark.parametrize("author_id", [_OTHER_BOT_ID, _UNKNOWN_BOT_ID])
def test_discord_report_when_sender_identity_is_forged_then_never_reaches_collection(
    author_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given
    roster_path = tmp_path / "roster.yaml"
    _write_roster(roster_path)
    monkeypatch.setenv("AUTOPHAGY_ROSTER", str(roster_path))
    marker = f"FORGED_REPORT_MARKER_{author_id}"
    _install_discord_fixture(
        monkeypatch,
        author_id=author_id,
        claimed_agent_id="peer-test",
        marker=marker,
    )
    client = RecordingMcpClient()

    # When
    with caplog.at_level(logging.WARNING, logger="autophagy.rag_ingest.discord_team"):
        pending, _log_lines = run_pipeline(_config(tmp_path), {"discord"}, client=client)

    # Then
    assert pending == 0
    assert client.loaded == []
    assert queuefile.load_jobs(tmp_path / "state" / "queue.jsonl") == []
    assert all(marker not in content for content, _source, _metadata in client.loaded)
    assert "discord peer report rejected" in caplog.text
