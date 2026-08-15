from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.interop.report import ReportStatus, TaskReport, format_report
from automation.report_hub.classify import (
    AGENT_ID_MISMATCH,
    REGISTERED,
    UNKNOWN_BOT,
    AcceptedReport,
    QuarantinedMessage,
    classify_message,
)
from automation.report_hub.dashboard import render_page
from automation.report_hub.registry import Peer, PeerRegistry, RegistryError, load_registry
from automation.report_hub.store import ReportQuery, ReportRow, ReportStore

AGENT_BOT_ID = "100000000000000001"
PEER_BOT_ID = "100000000000000002"
GHOST_BOT_ID = "100000000000000009"

REGISTRY = PeerRegistry(
    peers=(
        Peer(agent_id="agent-cha", bot_user_id=AGENT_BOT_ID, bot_name="Autophagy-Agent"),
        Peer(agent_id="peer-test", bot_user_id=PEER_BOT_ID, bot_name="Autophagy-Peer"),
    )
)


def _conformant_message(agent_id: str = "agent-cha") -> str:
    return format_report(
        TaskReport(
            agent_id=agent_id,
            task_id="W3-4",
            status=ReportStatus.DONE,
            summary="report hub fixture",
            links=("https://example.invalid/qa",),
            timestamp=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        )
    )


def test_classify_when_conformant_registered_author_then_accepted_registered() -> None:
    # When
    outcome = classify_message(_conformant_message(), AGENT_BOT_ID, REGISTRY)

    # Then
    assert isinstance(outcome, AcceptedReport)
    assert outcome.registered is True
    assert outcome.registration_note == REGISTERED
    assert outcome.report.task_id == "W3-4"


def test_classify_when_author_not_in_registry_then_accepted_unregistered() -> None:
    # When
    outcome = classify_message(_conformant_message(agent_id="ghost-agent"), GHOST_BOT_ID, REGISTRY)

    # Then
    assert isinstance(outcome, AcceptedReport)
    assert outcome.registered is False
    assert outcome.registration_note == UNKNOWN_BOT


def test_classify_when_registered_author_claims_foreign_agent_id_then_unregistered() -> None:
    # Given: the registered agent bot forges peer-test's agent_id
    forged = _conformant_message(agent_id="peer-test")

    # When
    outcome = classify_message(forged, AGENT_BOT_ID, REGISTRY)

    # Then
    assert isinstance(outcome, AcceptedReport)
    assert outcome.registered is False
    assert outcome.registration_note == AGENT_ID_MISMATCH


@pytest.mark.parametrize(
    "non_conformant",
    [
        "quarantine me: plain prose in #agents-log",
        "```json\n{\"version\": \"v0\"}\n```",
        '```json\n{"version": "v1", "agent_id": "a", "task_id": "t", "status": "done",'
        ' "summary": "s", "links": [], "timestamp": "2026-07-15T12:00:00+00:00"}\n```',
        '```json\n{"version": "v0", "agent_id": "a", "task_id": "t", "status": "pwned",'
        ' "summary": "s", "links": [], "timestamp": "2026-07-15T12:00:00+00:00"}\n```',
        '```json\n{"version": "v0", "agent_id": "a", "task_id": "t", "status": "done",'
        ' "summary": "s", "links": [], "timestamp": "2026-07-15T12:00:00+00:00",'
        ' "forged_extra": true}\n```',
        '```json\n{"version": "v0", "agent_id": "a", "task_id": "t", "status": "done",'
        ' "summary": "s", "links": [1], "timestamp": "2026-07-15T12:00:00+00:00"}\n```',
    ],
    ids=["prose", "missing-keys", "wrong-version", "forged-status", "forged-extra-key", "forged-link-type"],
)
def test_classify_when_non_conformant_or_forged_then_quarantined(non_conformant: str) -> None:
    # When
    outcome = classify_message(non_conformant, AGENT_BOT_ID, REGISTRY)

    # Then
    assert isinstance(outcome, QuarantinedMessage)
    assert outcome.reason == "non_conformant_protocol_message"


def _row(message_id: str, agent_id: str, status: str, registered: bool = True) -> ReportRow:
    return ReportRow(
        message_id=message_id,
        channel_id="200000000000000001",
        author_id=AGENT_BOT_ID if agent_id == "agent-cha" else PEER_BOT_ID,
        author_name="fixture-bot",
        agent_id=agent_id,
        task_id="W3-4",
        status=status,
        summary=f"summary {message_id}",
        links=(),
        report_timestamp=f"2026-07-15T12:00:0{message_id[-1]}+00:00",
        discord_timestamp="2026-07-15T12:00:00+00:00",
        registered=registered,
        registration_note="registered" if registered else "unknown_bot",
        collected_at="2026-07-15T12:01:00+00:00",
    )


def test_store_upsert_when_same_message_id_twice_then_single_row(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "reports.db"
    store = ReportStore(database)

    # When
    store.upsert_report(_row("1", "agent-cha", "start"))
    store.upsert_report(_row("1", "agent-cha", "done"))
    store.close()

    # Then
    query = ReportQuery(database)
    rows = query.reports()
    assert len(rows) == 1
    assert rows[0].status == "done"
    query.close()


def test_store_query_when_filtered_by_agent_and_status_then_matching_rows(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "reports.db"
    store = ReportStore(database)
    store.upsert_report(_row("1", "agent-cha", "done"))
    store.upsert_report(_row("2", "peer-test", "done"))
    store.upsert_report(_row("3", "agent-cha", "blocked"))
    store.close()

    # When
    query = ReportQuery(database)

    # Then
    assert {row.agent_id for row in query.reports(agent_id="agent-cha")} == {"agent-cha"}
    assert len(query.reports(agent_id="agent-cha")) == 2
    assert [row.message_id for row in query.reports(agent_id="agent-cha", status="done")] == ["1"]
    assert dict(query.counts_by_agent()) == {"agent-cha": 2, "peer-test": 1}
    assert dict(query.counts_by_status()) == {"done": 2, "blocked": 1}
    query.close()


def test_store_watermark_when_set_then_survives_reopen(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "reports.db"
    store = ReportStore(database)
    assert store.watermark() is None

    # When
    store.set_watermark("300000000000000007")
    store.close()

    # Then
    reopened = ReportStore(database)
    assert reopened.watermark() == "300000000000000007"
    reopened.close()


def test_dashboard_render_when_agent_filter_then_only_that_agents_rows(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "reports.db"
    store = ReportStore(database)
    store.upsert_report(_row("1", "agent-cha", "done"))
    store.upsert_report(_row("2", "peer-test", "start", registered=False))
    store.close()
    query = ReportQuery(database)

    # When
    filtered = render_page(query, agent_filter="agent-cha", status_filter="")
    unfiltered = render_page(query, agent_filter="", status_filter="")
    query.close()

    # Then
    assert 'data-agent="agent-cha"' in filtered
    assert 'data-agent="peer-test"' not in filtered
    assert 'data-agent="peer-test"' in unfiltered
    assert 'data-registered="unregistered"' in unfiltered


def test_load_registry_when_valid_file_then_lookup_by_bot_id(tmp_path: Path) -> None:
    # Given
    peers_file = tmp_path / "peers.yaml"
    peers_file.write_text(
        "version: 1\n"
        "peers:\n"
        "  agent-cha:\n"
        f'    bot_user_id: "{AGENT_BOT_ID}"\n'
        "    bot_name: Autophagy-Agent\n"
        "    account: agent\n",
        encoding="utf-8",
    )

    # When
    registry = load_registry(peers_file)

    # Then
    assert registry.agent_id_for(AGENT_BOT_ID) == "agent-cha"
    assert registry.agent_id_for(GHOST_BOT_ID) is None


@pytest.mark.parametrize(
    "content",
    [
        "version: 2\npeers:\n  a:\n    bot_user_id: \"1\"\n    bot_name: x\n",
        "version: 1\npeers: {}\n",
        "version: 1\npeers:\n  a:\n    bot_user_id: not-a-number\n    bot_name: x\n",
    ],
    ids=["wrong-version", "empty-peers", "non-numeric-id"],
)
def test_load_registry_when_malformed_then_registry_error(tmp_path: Path, content: str) -> None:
    # Given
    peers_file = tmp_path / "peers.yaml"
    peers_file.write_text(content, encoding="utf-8")

    # Then
    with pytest.raises(RegistryError):
        load_registry(peers_file)
