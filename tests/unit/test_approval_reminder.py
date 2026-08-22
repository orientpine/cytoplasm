from __future__ import annotations

import json
import multiprocessing as mp
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from automation.interop.approval_lease import (
    FileKeyLease,
    ReminderJournal,
    ReminderJournalError,
)
from automation.interop.approval_lifecycle import ApprovalRequest
from automation.interop.approval_reminder import (
    ReminderConfig,
    ReminderStatus,
    ReminderOutcome,
    dispatch_due_reminder,
    due_slot,
)

KEY = "mail:compose:42"
POSTED = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
CONFIG = ReminderConfig(initial_delay=timedelta(hours=3), repeat_interval=timedelta(hours=1))


def _request(message_id: str = "message-1", created_at: str = "2026-08-14T00:00:00Z") -> ApprovalRequest:
    return ApprovalRequest(KEY, "sha256:bound", message_id, "owner-dm", created_at)


class Observer:
    def __init__(self, status: ReminderStatus = ReminderStatus.PENDING) -> None:
        self.status = status
        self.calls = 0

    def status_for(self, request: ApprovalRequest) -> ReminderStatus:
        self.calls += 1
        return self.status


class Sender:
    def __init__(self) -> None:
        self.slots: list[int] = []

    def send(self, request: ApprovalRequest, slot: int, due_at: datetime) -> None:
        self.slots.append(slot)


def _dispatch(tmp_path: Path, now: datetime, observer: Observer | None = None, sender: Sender | None = None,
              request: ApprovalRequest | None = None):
    observer = observer or Observer()
    sender = sender or Sender()
    verdict = dispatch_due_reminder(
        request or _request(), observer, sender,
        FileKeyLease(tmp_path / "leases"), ReminderJournal(tmp_path / "reminders"), CONFIG,
        clock=lambda: now,
    )
    return verdict, observer, sender


def test_due_boundaries_are_anchored_to_original_post_time(tmp_path: Path) -> None:
    verdict, _, sender = _dispatch(tmp_path, POSTED + timedelta(hours=2, minutes=59, seconds=59))
    assert verdict.outcome is ReminderOutcome.NOT_DUE and sender.slots == []

    verdict, _, sender = _dispatch(tmp_path, POSTED + timedelta(hours=3))
    assert (verdict.outcome, verdict.slot, sender.slots) == (ReminderOutcome.SENT, 0, [0])

    verdict, _, sender = _dispatch(tmp_path, POSTED + timedelta(hours=4))
    assert (verdict.outcome, verdict.slot, sender.slots) == (ReminderOutcome.SENT, 1, [1])


def test_due_slot_uses_wall_clock_anchor_without_cumulative_drift() -> None:
    assert due_slot(POSTED, POSTED + timedelta(hours=2, minutes=59), CONFIG) is None
    assert due_slot(POSTED, POSTED + timedelta(hours=3), CONFIG) == 0
    assert due_slot(POSTED, POSTED + timedelta(hours=5, minutes=47), CONFIG) == 2


def test_catch_up_emits_latest_slot_once_instead_of_flooding_history(tmp_path: Path) -> None:
    now = POSTED + timedelta(hours=5, minutes=47)
    first, _, sender = _dispatch(tmp_path, now)
    second, _, sender = _dispatch(tmp_path, now, sender=sender)
    assert (first.outcome, first.slot) == (ReminderOutcome.SENT, 2)
    assert second.outcome is ReminderOutcome.ALREADY_CLAIMED
    assert sender.slots == [2]


def test_restart_keeps_claim_and_sent_history(tmp_path: Path) -> None:
    now = POSTED + timedelta(hours=3)
    first, _, _ = _dispatch(tmp_path, now)
    assert first.outcome is ReminderOutcome.SENT

    restarted_store = ReminderJournal(tmp_path / "reminders")
    sender = Sender()
    verdict = dispatch_due_reminder(
        _request(), Observer(), sender, FileKeyLease(tmp_path / "leases"), restarted_store,
        CONFIG, clock=lambda: now,
    )
    assert verdict.outcome is ReminderOutcome.ALREADY_CLAIMED and sender.slots == []
    history = restarted_store.history(KEY, "message-1")
    assert [(item.slot, item.state) for item in history] == [(0, "sent")]


def test_corrupt_persistent_state_fails_closed_without_send(tmp_path: Path) -> None:
    journal = ReminderJournal(tmp_path / "reminders")
    path = journal._path(KEY, "message-1")
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    sender = Sender()
    with pytest.raises(ReminderJournalError):
        dispatch_due_reminder(
            _request(), Observer(), sender, FileKeyLease(tmp_path / "leases"), journal,
            CONFIG, clock=lambda: POSTED + timedelta(hours=3),
        )
    assert sender.slots == []


@pytest.mark.parametrize("status", [
    ReminderStatus.APPROVED,
    ReminderStatus.CANCELLED,
    ReminderStatus.EXECUTED,
    ReminderStatus.DISCARDED,
    ReminderStatus.EXPIRED,
    ReminderStatus.SUPERSEDED,
    ReminderStatus.SOURCE_DELETED,
])
def test_terminal_lifecycle_observation_retires_immediately_and_survives_restart(
    tmp_path: Path, status: ReminderStatus,
) -> None:
    first, _, sender = _dispatch(tmp_path, POSTED + timedelta(hours=9), Observer(status))
    assert first.outcome is ReminderOutcome.RETIRED and sender.slots == []

    second, _, sender = _dispatch(tmp_path, POSTED + timedelta(hours=10), Observer(), sender=sender)
    assert second.outcome is ReminderOutcome.RETIRED and sender.slots == []


def test_unverifiable_lifecycle_fails_closed_without_retiring(tmp_path: Path) -> None:
    verdict, _, sender = _dispatch(
        tmp_path, POSTED + timedelta(hours=3), Observer(ReminderStatus.UNVERIFIABLE)
    )
    assert verdict.outcome is ReminderOutcome.DEFERRED and sender.slots == []
    retry, _, sender = _dispatch(tmp_path, POSTED + timedelta(hours=3), Observer(), sender=sender)
    assert retry.outcome is ReminderOutcome.SENT and sender.slots == [0]


def test_superseded_message_does_not_poison_new_message_with_same_key(tmp_path: Path) -> None:
    old, _, _ = _dispatch(
        tmp_path, POSTED + timedelta(hours=7), Observer(ReminderStatus.SUPERSEDED),
        request=_request("old"),
    )
    new_request = _request("new", "2026-08-14T04:00:00Z")
    new, _, sender = _dispatch(tmp_path, POSTED + timedelta(hours=7), request=new_request)
    assert old.outcome is ReminderOutcome.RETIRED
    assert (new.outcome, new.slot, sender.slots) == (ReminderOutcome.SENT, 0, [0])


def test_disabled_config_never_probes_or_sends(tmp_path: Path) -> None:
    observer, sender = Observer(), Sender()
    config = ReminderConfig(enabled=False)
    verdict = dispatch_due_reminder(
        _request(), observer, sender, FileKeyLease(tmp_path / "leases"),
        ReminderJournal(tmp_path / "reminders"), config, clock=lambda: POSTED + timedelta(days=1),
    )
    assert verdict.outcome is ReminderOutcome.DISABLED
    assert observer.calls == 0 and sender.slots == []


def test_invalid_intervals_are_rejected() -> None:
    with pytest.raises(ValueError):
        ReminderConfig(initial_delay=timedelta(hours=-1))
    with pytest.raises(ValueError):
        ReminderConfig(repeat_interval=timedelta(0))


def test_send_failure_keeps_claim_to_prevent_uncertain_duplicate(tmp_path: Path) -> None:
    class FailingSender(Sender):
        def send(self, request: ApprovalRequest, slot: int, due_at: datetime) -> None:
            raise OSError("offline")

    with pytest.raises(OSError):
        _dispatch(tmp_path, POSTED + timedelta(hours=3), sender=FailingSender())
    retry, _, sender = _dispatch(tmp_path, POSTED + timedelta(hours=3))
    assert retry.outcome is ReminderOutcome.ALREADY_CLAIMED and sender.slots == []

    next_slot, _, sender = _dispatch(tmp_path, POSTED + timedelta(hours=4), sender=sender)
    assert next_slot.outcome is ReminderOutcome.SENT and sender.slots == [1]


def _concurrent_worker(root: str, barrier, output) -> None:
    class FileSender:
        def send(self, request: ApprovalRequest, slot: int, due_at: datetime) -> None:
            with (Path(root) / "sends.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"message": request.message_id, "slot": slot}) + "\n")

    barrier.wait(timeout=5)
    verdict = dispatch_due_reminder(
        _request(), Observer(), FileSender(), FileKeyLease(Path(root) / "leases"),
        ReminderJournal(Path(root) / "reminders"), CONFIG,
        clock=lambda: POSTED + timedelta(hours=3),
    )
    output.put(verdict.outcome.value)


def test_concurrent_watchers_send_one_reminder_for_same_identity_and_slot(tmp_path: Path) -> None:
    ctx = mp.get_context("fork")
    barrier, output = ctx.Barrier(2), ctx.Queue()
    workers = [ctx.Process(target=_concurrent_worker, args=(str(tmp_path), barrier, output)) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert worker.exitcode == 0
    outcomes = sorted(output.get(timeout=2) for _ in workers)
    assert outcomes.count(ReminderOutcome.SENT.value) == 1
    assert set(outcomes) <= {
        ReminderOutcome.SENT.value,
        ReminderOutcome.SKIPPED.value,
        ReminderOutcome.ALREADY_CLAIMED.value,
    }
    assert len((tmp_path / "sends.jsonl").read_text(encoding="utf-8").splitlines()) == 1
