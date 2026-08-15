from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import pytest

from automation.repair import repair_cli
from automation.repair.repair_cli import HermesKanban
from automation.repair.repair_core import RepairEvent, RepairRegistry, RepairService


_CARD_JSON: Final = '{"task":{"id":"t_repair01"}}'


@dataclass
class FakeKanban:
    created: list[tuple[str, str, str]] = field(default_factory=list)
    comments: list[tuple[str, str]] = field(default_factory=list)
    blocked: list[tuple[str, str]] = field(default_factory=list)

    def create(self, title: str, body: str, idempotency_key: str) -> str:
        self.created.append((title, body, idempotency_key))
        return "t_repair01"

    def comment(self, ticket_id: str, text: str) -> None:
        self.comments.append((ticket_id, text))

    def block_for_repair(self, ticket_id: str, reason: str) -> None:
        self.blocked.append((ticket_id, reason))


@dataclass
class FakePrivateLogs:
    entries: list[tuple[str, int, str]] = field(default_factory=list)

    def write(self, ticket_id: str, occurrence: int, raw_log: str) -> str:
        self.entries.append((ticket_id, occurrence, raw_log))
        return f"/srv/autophagy-private/repair-logs/{ticket_id}/occurrence-{occurrence}.log"


def test_hermes_kanban_create_omits_triage_so_card_can_be_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the production adapter is observed at its subprocess boundary.
    calls: list[tuple[str, ...]] = []

    def capture(*args: str) -> str:
        calls.append(args)
        return _CARD_JSON

    monkeypatch.setattr(repair_cli.HermesKanban, "_run", staticmethod(capture))

    # When: it creates a repair ticket before RepairService applies needs_input.
    ticket_id = HermesKanban().create("repair title", "repair body", "repair-key")

    # Then: the CLI receives a default-ready create request rather than an unblockable triage card.
    assert ticket_id == "t_repair01"
    assert "--triage" not in calls[0]


def test_creates_blocked_repair_ticket_with_redacted_path_and_hash(tmp_path: Path) -> None:
    # Given: a skill failure that includes a token-shaped secret.
    service = RepairService(FakeKanban(), FakePrivateLogs(), RepairRegistry(tmp_path / "repair-state.json"))
    event = RepairEvent(source="skill:report", location="report_cli.py:42", raw_log="RuntimeError sk-secretvalue")

    # When: the detector records it.
    result = service.record(event)

    # Then: the ticket data is masked while the full raw log is only handed to the private sink.
    assert result.ticket_id == "t_repair01"
    assert result.occurrence == 1
    assert result.created is True
    assert "sk-secretvalue" not in result.excerpt
    assert result.private_path.endswith("t_repair01/occurrence-1.log")
    assert len(result.log_hash) == 64


def test_recurring_error_adds_occurrence_without_creating_duplicate(tmp_path: Path) -> None:
    # Given: a previously recorded normalized failure.
    kanban = FakeKanban()
    private_logs = FakePrivateLogs()
    service = RepairService(kanban, private_logs, RepairRegistry(tmp_path / "repair-state.json"))
    event = RepairEvent(source="healthcheck", location="agent gateway", raw_log="FAIL agent gateway")
    first = service.record(event)

    # When: the same failure reoccurs.
    repeated = service.record(event)

    # Then: its original ticket is retained and its occurrence count advances.
    assert repeated.ticket_id == first.ticket_id
    assert repeated.created is False
    assert repeated.occurrence == 2
    assert len(kanban.created) == 1
    assert private_logs.entries[-1][1] == 2


def test_ticket_excerpt_and_comment_never_include_sensitive_error_text(tmp_path: Path) -> None:
    # Given: a bearer token in an error log.
    kanban = FakeKanban()
    service = RepairService(kanban, FakePrivateLogs(), RepairRegistry(tmp_path / "repair-state.json"))

    # When: the event is persisted.
    service.record(RepairEvent(source="skill:repair", location="repair_cli.py:1", raw_log="Bearer secret-value"))

    # Then: all Kanban-visible strings omit the sensitive value and include linkage instead.
    visible = "\n".join(text for _, text in kanban.comments) + "\n" + kanban.created[0][1]
    assert "secret-value" not in visible
    assert "sha256=" in visible
    assert "/srv/autophagy-private/repair-logs/t_repair01/" in visible
