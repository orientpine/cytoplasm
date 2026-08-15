from __future__ import annotations

# noqa: SIZE_OK — table-driven acceptance suite keeps the 26-item contract together.

import hashlib
import json
import os
import threading
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.repair import repair_report_reconcile
from automation.repair.repair_lifecycle import LifecycleState, RepairLifecycleStore
from automation.repair.repair_ops_reporting import HermesTicketBoard
from automation.repair.repair_report_queue import (
    ReportRequest,
    enqueue_if_missing_semantic,
    parse_line,
    semantic_key,
)


TICKET = "t_reconcile1"
MAC = "a" * 64


@pytest.fixture(autouse=True)
def _isolated_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    state = tmp_path / "state"
    capability = tmp_path / "capability"
    queue = tmp_path / "queue"
    ack = tmp_path / "ack"
    for directory in (state, capability, queue, ack):
        directory.mkdir()
    (queue / "queue.lock").touch(mode=0o640)
    monkeypatch.setenv("REPAIR_STATE_ROOT", str(state))
    monkeypatch.setenv("REPAIR_CAPABILITY_DIR", str(capability))
    monkeypatch.setenv("REPAIR_REPORT_QUEUE", str(queue))
    monkeypatch.setenv("REPAIR_REPORT_ACK", str(ack))


def _state_root() -> Path:
    return repair_report_reconcile.lifecycle_root()


def _queue_root() -> Path:
    return Path(os.environ["REPAIR_REPORT_QUEUE"])


def _ack_root() -> Path:
    return Path(os.environ["REPAIR_REPORT_ACK"])


def _write_lifecycle(
    state: str = "done",
    reason: str = "commit-deadbeef",
    *,
    ticket_id: str = TICKET,
    updated_at: str = "2026-08-07T00:00:00Z",
) -> Path:
    target = _state_root() / f"{ticket_id}.json"
    target.write_text(
        json.dumps(
            {
                "ticket_id": ticket_id,
                "state": state,
                "reason": reason,
                "updated_at": updated_at,
                "sandbox_checks": "",
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return target


def _write_capability(occurrence: str = "1", *, ticket_id: str = TICKET) -> None:
    root = Path(os.environ["REPAIR_CAPABILITY_DIR"])
    (root / f"{ticket_id}.json").write_text(
        json.dumps(
            {
                "ticket_id": ticket_id,
                "occurrence": occurrence,
                "mac": MAC,
                "issued_at": "1",
            }
        ),
        encoding="utf-8",
    )


def _queued() -> list[ReportRequest]:
    pending = _queue_root() / "pending.jsonl"
    if not pending.exists():
        return []
    requests = [parse_line(raw) for raw in pending.read_bytes().splitlines()]
    return [request for request in requests if request is not None]


def _report_key(ticket_id: str, operation: str, reason_code: str) -> str:
    return hashlib.sha256(f"{ticket_id}|{operation}|{reason_code}".encode()).hexdigest()


@pytest.mark.parametrize(
    ("state", "reason", "operation", "reason_code"),
    [
        ("done", "commit-deadbeef", "complete", "applied"),
        ("reopened", "sandbox gate rejected", "reopen", "sandbox_rejected"),
        ("reopened", "regression bank failed; patch reverted", "reopen", "bank_failed_reverted"),
        ("reopened", "owner_cancelled", "reopen", "owner_cancelled"),
        ("reopened", "approval_expired", "reopen", "approval_expired"),
        ("reopened", "future reason", "reopen", "unspecified"),
    ],
)
def test_terminal_lifecycle_maps_to_one_enum_only_request(
    state: str,
    reason: str,
    operation: str,
    reason_code: str,
) -> None:
    # Given
    _write_lifecycle(state, reason)
    _write_capability()

    # When
    count = repair_report_reconcile.reconcile()

    # Then
    assert count == 1
    assert [(item.operation, item.reason_code) for item in _queued()] == [(operation, reason_code)]


@pytest.mark.parametrize("reason", ["owner_cancelled", "approval_expired"])
def test_discard_path_and_reconcile_share_one_semantic_request(reason: str) -> None:
    # Given
    _write_capability()
    lifecycle = RepairLifecycleStore(_state_root())
    lifecycle.transition(TICKET, LifecycleState.REOPENED, reason)

    # When
    HermesTicketBoard().reopen(TICKET, reason)
    repair_report_reconcile.reconcile()

    # Then
    keys = [semantic_key(request) for request in _queued()]
    assert len(keys) == 1
    assert keys == [semantic_key(_queued()[0])]


@pytest.mark.parametrize("state", ["awaiting_approval", "applied", "sandboxed"])
def test_nonterminal_lifecycle_is_skipped(state: str) -> None:
    _write_lifecycle(state)
    _write_capability()

    assert repair_report_reconcile.reconcile() == 0
    assert _queued() == []


def test_semantic_receipt_suppresses_enqueue() -> None:
    _write_lifecycle()
    _write_capability()
    request = ReportRequest("0" * 32, "complete", TICKET, "applied", "1", MAC, datetime.now(UTC).isoformat())
    (_ack_root() / f"sem-{semantic_key(request)}.json").write_text("{}", encoding="utf-8")

    assert repair_report_reconcile.reconcile() == 0


def test_pending_semantic_request_suppresses_enqueue() -> None:
    _write_lifecycle()
    _write_capability()
    request = ReportRequest("0" * 32, "complete", TICKET, "applied", "1", MAC, datetime.now(UTC).isoformat())
    assert enqueue_if_missing_semantic(request)

    assert repair_report_reconcile.reconcile() == 0
    assert len(_queued()) == 1


def test_missing_capability_skips_only_that_ticket() -> None:
    _write_lifecycle(ticket_id="t_missing")
    _write_lifecycle(ticket_id="t_present")
    _write_capability(ticket_id="t_present")

    assert repair_report_reconcile.reconcile() == 1
    assert [request.ticket_id for request in _queued()] == ["t_present"]


def test_report_marker_suppresses_new_occurrence_and_accepted_second_loss() -> None:
    _write_lifecycle()
    _write_capability("1")
    key = _report_key(TICKET, "complete", "applied")
    (_ack_root() / f"reported-{key}.json").write_text("{}", encoding="utf-8")
    _write_capability("2")

    assert repair_report_reconcile.reconcile() == 0
    assert _queued() == []


def test_new_terminal_reason_after_marker_enqueues_again() -> None:
    _write_lifecycle()
    _write_capability("2")
    old_key = _report_key(TICKET, "complete", "applied")
    (_ack_root() / f"reported-{old_key}.json").write_text("{}", encoding="utf-8")
    _write_lifecycle("reopened", "owner_cancelled")

    assert repair_report_reconcile.reconcile() == 1
    assert _queued()[0].reason_code == "owner_cancelled"


@pytest.mark.parametrize("updated_at", ["1970-01-01T00:00:00Z", "2999-01-01T00:00:00Z"])
def test_marker_suppression_ignores_lifecycle_timestamp(updated_at: str) -> None:
    _write_lifecycle(updated_at=updated_at)
    _write_capability()
    key = _report_key(TICKET, "complete", "applied")
    (_ack_root() / f"reported-{key}.json").write_text("{}", encoding="utf-8")

    assert repair_report_reconcile.reconcile() == 0


def test_two_calls_are_idempotent() -> None:
    _write_lifecycle()
    _write_capability()

    assert repair_report_reconcile.reconcile() == 1
    assert repair_report_reconcile.reconcile() == 0
    assert len(_queued()) == 1


def test_reconcile_returns_without_reentrant_lock_deadlock() -> None:
    _write_lifecycle()
    _write_capability()
    result: list[int] = []
    worker = threading.Thread(target=lambda: result.append(repair_report_reconcile.reconcile()))

    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert result == [1]


def test_parallel_reconcilers_and_live_enqueue_append_one_semantic_line() -> None:
    _write_lifecycle()
    _write_capability()
    barrier = threading.Barrier(3)
    live_request = ReportRequest(
        "f" * 32,
        "complete",
        TICKET,
        "applied",
        "1",
        MAC,
        datetime.now(UTC).isoformat(),
    )

    def reconcile_worker() -> None:
        barrier.wait()
        repair_report_reconcile.reconcile()

    def live_worker() -> None:
        barrier.wait()
        enqueue_if_missing_semantic(live_request)

    workers = [threading.Thread(target=reconcile_worker) for _ in range(2)] + [threading.Thread(target=live_worker)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert all(not worker.is_alive() for worker in workers)
    assert len(_queued()) == 1


def test_raw_lifecycle_reason_never_reaches_queue_or_log(capsys: pytest.CaptureFixture[str]) -> None:
    raw_reason = "commit-deadbeef-private"
    _write_lifecycle(reason=raw_reason)
    _write_capability()

    repair_report_reconcile.reconcile()

    assert raw_reason not in (_queue_root() / "pending.jsonl").read_text(encoding="utf-8")
    assert raw_reason not in capsys.readouterr().err
    assert raw_reason not in json.dumps([asdict(request) for request in _queued()])


@pytest.mark.parametrize("damaged", [False, True])
def test_missing_or_damaged_lifecycle_returns_zero_without_exception(damaged: bool) -> None:
    if damaged:
        (_state_root() / f"{TICKET}.json").write_bytes(b"{broken")
    else:
        _state_root().rmdir()

    assert repair_report_reconcile.reconcile() == 0


def test_limit_defers_excess_lifecycle_files_to_a_later_call() -> None:
    first = _write_lifecycle(ticket_id="t_a")
    _write_lifecycle(ticket_id="t_b")
    _write_capability(ticket_id="t_a")
    _write_capability(ticket_id="t_b")

    assert repair_report_reconcile.reconcile(limit=1) == 1
    first.unlink()
    assert repair_report_reconcile.reconcile(limit=1) == 1
    assert [request.ticket_id for request in _queued()] == ["t_a", "t_b"]


def test_board_swallows_reconcile_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_capability()

    def fail_reconcile(*, limit: int = 50) -> int:
        del limit
        raise OSError("reconcile unavailable")

    monkeypatch.setattr(repair_report_reconcile, "reconcile", fail_reconcile)

    assert HermesTicketBoard().complete(TICKET, "private summary") is None


def test_reconcile_never_mutates_lifecycle_or_queue_lock_inode() -> None:
    lifecycle = _write_lifecycle()
    _write_capability()
    before = lifecycle.read_bytes()
    lock = _queue_root() / "queue.lock"
    inode = lock.stat().st_ino

    repair_report_reconcile.reconcile()

    assert lifecycle.read_bytes() == before
    assert lock.stat().st_ino == inode


def test_standalone_main_recovers_without_board_call(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _write_lifecycle()
    _write_capability()

    def reject_board() -> None:
        raise AssertionError("board must not be used")

    monkeypatch.setattr("automation.repair.repair_ops_reporting.HermesTicketBoard", reject_board)

    assert repair_report_reconcile._main([]) == 0
    assert capsys.readouterr().out.strip() == "1"
    assert len(_queued()) == 1


def test_all_roots_are_test_overrides_not_srv() -> None:
    assert not str(_state_root()).startswith("/srv/")
    assert not str(_queue_root()).startswith("/srv/")
    assert not str(_ack_root()).startswith("/srv/")
