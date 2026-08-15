from __future__ import annotations

import subprocess
from dataclasses import asdict
from datetime import datetime

import pytest

from automation.repair import repair_ops_reporting, repair_report_reconcile
from automation.repair.repair_report_queue import ReportRequest


CAPABILITY = {
    "ticket_id": "t_repair01",
    "occurrence": "7",
    "mac": "a" * 64,
    "issued_at": "1",
}


def install_reporting_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    capability: dict[str, str] | None = CAPABILITY,
    failing_step: str | None = None,
) -> tuple[list[ReportRequest], dict[str, int]]:
    queued: list[ReportRequest] = []
    calls = {"read": 0, "enqueue": 0, "compact": 0, "reconcile": 0}

    def read_published(_ticket_id: str) -> dict[str, str] | None:
        calls["read"] += 1
        if failing_step == "read":
            raise OSError("read unavailable")
        return capability

    def enqueue(request: ReportRequest) -> bool:
        calls["enqueue"] += 1
        if failing_step == "enqueue":
            raise OSError("enqueue unavailable")
        queued.append(request)
        return True

    def compact() -> int:
        calls["compact"] += 1
        if failing_step == "compact":
            raise OSError("compact unavailable")
        return 0

    def reconcile() -> int:
        calls["reconcile"] += 1
        if failing_step == "reconcile":
            raise OSError("reconcile unavailable")
        return 0

    monkeypatch.setattr(repair_ops_reporting, "read_published", read_published)
    monkeypatch.setattr(repair_ops_reporting, "enqueue_if_missing_semantic", enqueue)
    monkeypatch.setattr(repair_ops_reporting, "compact", compact)

    # Patch the real module object because package attributes survive sys.modules replacement.
    monkeypatch.setattr(repair_report_reconcile, "reconcile", reconcile)
    return queued, calls


def test_complete_when_capability_exists_then_enqueues_bound_enum_only_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a published capability and observable queue/report stages.
    queued, calls = install_reporting_fakes(monkeypatch)
    summary = "repair applied abc123; 2026-08-07-t_repair01.md"

    # When: the board receives a completion summary containing sensitive metadata.
    repair_ops_reporting.HermesTicketBoard().complete("t_repair01", summary)

    # Then: only the capability-bound enum request reaches the queue.
    assert len(queued) == 1
    request = queued[0]
    assert request.operation == "complete"
    assert request.reason_code == "applied"
    assert request.occurrence == "7"
    assert request.mac == "a" * 64
    assert len(request.request_id) == 32
    assert datetime.fromisoformat(request.created).utcoffset() is not None
    assert summary not in str(asdict(request))
    assert "abc123" not in str(asdict(request))
    assert "2026-08-07-t_repair01.md" not in str(asdict(request))
    assert calls == {"read": 1, "enqueue": 1, "compact": 1, "reconcile": 1}


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ("sandbox gate rejected; no patch applied", "sandbox_rejected"),
        ("regression bank state is red; no patch applied", "bank_red"),
        ("regression bank failed; patch reverted", "bank_failed_reverted"),
        ("owner_cancelled", "owner_cancelled"),
        ("approval_expired", "approval_expired"),
        ("unexpected reason", "unspecified"),
    ],
)
def test_reopen_when_summary_received_then_enqueues_only_normalized_reason(
    monkeypatch: pytest.MonkeyPatch,
    summary: str,
    expected: str,
) -> None:
    # Given: a published capability and one lifecycle summary.
    queued, calls = install_reporting_fakes(monkeypatch)

    # When: the board reopens the ticket.
    repair_ops_reporting.HermesTicketBoard().reopen("t_repair01", summary)

    # Then: the queue receives the mapped enum and each follow-up runs once.
    assert queued[0].operation == "reopen"
    assert queued[0].reason_code == expected
    assert summary not in str(asdict(queued[0])) or summary == expected
    assert calls == {"read": 1, "enqueue": 1, "compact": 1, "reconcile": 1}


def test_board_when_capability_is_absent_then_defers_without_enqueue(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: capability publication has not reached the ops reader yet.
    queued, calls = install_reporting_fakes(monkeypatch, capability=None)

    # When: the terminal lifecycle event reaches the board during that gap.
    result = repair_ops_reporting.HermesTicketBoard().complete("t_repair01", "private summary")

    # Then: reporting is delayed without leaking the ticket or summary.
    stderr = capsys.readouterr().err
    assert result is None
    assert queued == []
    assert calls == {"read": 1, "enqueue": 0, "compact": 0, "reconcile": 0}
    assert "capability unavailable" in stderr
    assert "t_repair01" not in stderr
    assert "private summary" not in stderr


@pytest.mark.parametrize("failing_step", ["read", "enqueue", "compact", "reconcile"])
def test_board_when_reporting_step_raises_then_lifecycle_result_remains_non_fatal(
    monkeypatch: pytest.MonkeyPatch,
    failing_step: str,
) -> None:
    # Given: one reporting boundary fails independently.
    install_reporting_fakes(monkeypatch, failing_step=failing_step)

    # When: the board records a terminal event.
    result = repair_ops_reporting.HermesTicketBoard().complete("t_repair01", "repair applied")

    # Then: the optional reporting path never changes the repair result.
    assert result is None


def test_board_when_invoked_then_never_calls_subprocess_or_sudo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: any subprocess call would expose the removed ops-to-agent mutation path.
    queued, _calls = install_reporting_fakes(monkeypatch)

    def reject_subprocess(*_args: str, **_kwargs: str) -> None:
        raise AssertionError("subprocess is forbidden")

    monkeypatch.setattr(subprocess, "run", reject_subprocess)

    # When: both board operations are requested.
    board = repair_ops_reporting.HermesTicketBoard()
    board.complete("t_repair01", "repair applied")
    board.reopen("t_repair01", "owner_cancelled")

    # Then: both requests enqueue without any subprocess or sudo boundary.
    assert [request.operation for request in queued] == ["complete", "reopen"]
