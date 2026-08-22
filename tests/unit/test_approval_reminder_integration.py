"""Shared approval-reminder façade and existing-watcher wiring regressions."""
from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

from automation.interop.approval_lease import FileKeyLease, ReminderJournal
from automation.interop.approval_lifecycle import ApprovalRequest, Probe, remind_owner_approval
from automation.interop.approval_reminder import ReminderContext, ReminderOutcome
from automation.interop.approval_reminder_config import ApprovalReminderConfig
from automation.interop.approval_surface import ApprovalKind

_POSTED = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)
_REQUEST = ApprovalRequest(
    key="mail:compose:42",
    action_hash="sha256:bound",
    message_id="333",
    channel_id="222",
    created_at="2026-08-21T00:00:00Z",
)


class Observer:
    def __init__(self, decision: Probe = Probe.BOUND_PENDING) -> None:
        self.decision = decision
        self.calls = 0

    def probe(self, request: ApprovalRequest) -> Probe:
        assert request == _REQUEST
        self.calls += 1
        return self.decision


class Delivery:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def __call__(self, channel_id: str, content: str) -> None:
        self.messages.append((channel_id, content))


def _context(tmp_path: Path, delivery: Delivery, now: datetime) -> ReminderContext:
    return ReminderContext(
        config=ApprovalReminderConfig(),
        journal=ReminderJournal(tmp_path / "reminders"),
        request_type=ApprovalKind.MAIL_COMPOSE,
        deliver=delivery,
        clock=lambda: now,
    )


def test_shared_facade_sends_minimum_information_pointer_at_three_hours(tmp_path: Path) -> None:
    delivery = Delivery()
    verdict = remind_owner_approval(
        _REQUEST,
        Observer(),
        FileKeyLease(tmp_path / "leases"),
        _context(tmp_path, delivery, _POSTED + timedelta(hours=3)),
    )

    assert verdict.outcome is ReminderOutcome.SENT
    assert delivery.messages == [
        (
            "222",
            "승인 리마인더\n"
            "요청 유형: mail-compose\n"
            "경과시간: 3시간\n"
            "원문 링크: https://discord.com/channels/@me/222/333",
        )
    ]


def test_shared_facade_maps_uncertain_probe_fail_closed(tmp_path: Path) -> None:
    delivery = Delivery()
    verdict = remind_owner_approval(
        _REQUEST,
        Observer(Probe.BINDING_MISMATCH),
        FileKeyLease(tmp_path / "leases"),
        _context(tmp_path, delivery, _POSTED + timedelta(hours=4)),
    )
    assert verdict.outcome is ReminderOutcome.DEFERRED
    assert delivery.messages == []


def test_disabled_surface_creates_no_journal_probe_or_send(tmp_path: Path) -> None:
    observer, delivery = Observer(), Delivery()
    context = ReminderContext(
        config=ApprovalReminderConfig(enabled=False),
        journal=ReminderJournal(tmp_path / "reminders"),
        request_type=ApprovalKind.MAIL_COMPOSE,
        deliver=delivery,
        clock=lambda: _POSTED + timedelta(days=1),
    )
    verdict = remind_owner_approval(
        _REQUEST, observer, FileKeyLease(tmp_path / "leases"), context
    )
    assert verdict.outcome is ReminderOutcome.DISABLED
    assert observer.calls == 0
    assert delivery.messages == []
    assert not (tmp_path / "reminders").exists()
    assert not (tmp_path / "leases").exists()


def test_existing_approval_watchers_call_the_shared_facade() -> None:
    root = Path(__file__).parents[2]
    watchers = (
        root / "skills/mail/scripts/triage_cli.py",
        root / "skills/calendar/scripts/confirm_reaction_watch.py",
        root / "skills/todo/scripts/todo_confirm_reaction_watch.py",
        root / "automation/repair/repair_ops_reaction_watch.py",
    )
    for path in watchers:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = (node for node in ast.walk(tree) if isinstance(node, ast.Call))
        assert any(
            (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "remind_owner_approval"
            )
            or (
                isinstance(call.func, ast.Name)
                and call.func.id == "remind_owner_approval"
            )
            for call in calls
        ), path
