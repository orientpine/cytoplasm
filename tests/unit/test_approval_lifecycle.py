from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import assert_never

import pytest

from automation.interop.approval_lease import PostingJournal
from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalRecordsError,
    ApprovalRequest,
    ApprovalSurfaceError,
    Outcome,
    PostedApproval,
    Probe,
    Reason,
    Verdict,
    WatchOutcome,
    request_owner_approval,
    resolve_owner_decision,
)

KEY = "drive:project/a"
INTENT = ApprovalIntent(KEY, "new", "channel")


def _request(message_id: str, action_hash: str = "old", created_at: str = "2026-01-01T00:00:00Z") -> ApprovalRequest:
    return ApprovalRequest(KEY, action_hash, message_id, "channel", created_at)


class FakeLease:
    def __init__(self, owned: bool = True) -> None:
        self.owned = owned

    @contextmanager
    def hold(self, key: str) -> Iterator[bool]:
        yield self.owned


class FakeGate:
    def __init__(self, records: tuple[ApprovalRequest, ...] = ()) -> None:
        self.records = list(records)
        self.probes: dict[str, list[Probe | ApprovalSurfaceError | OSError]] = {}
        self.calls: list[str] = []
        self.records_error = False
        self.delete_error: ApprovalSurfaceError | OSError | None = None
        self.posts = 0
        self.journal: PostingJournal | None = None
        self.saw_reservation = False

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        self.calls.append("outstanding")
        if self.records_error:
            raise ApprovalRecordsError
        return tuple(self.records)

    def probe(self, request: ApprovalRequest) -> Probe:
        self.calls.append(f"probe:{request.message_id}")
        values = self.probes.get(request.message_id)
        value = values.pop(0) if values else Probe.BOUND_PENDING
        match value:
            case Probe() as probe:
                return probe
            case ApprovalSurfaceError() | OSError() as error:
                raise error
            case unreachable:
                assert_never(unreachable)

    def delete(self, request: ApprovalRequest) -> None:
        self.calls.append(f"delete:{request.message_id}")
        if self.delete_error is not None:
            raise self.delete_error

    def drop(self, request: ApprovalRequest) -> None:
        self.calls.append(f"drop:{request.message_id}")
        if request in self.records:
            self.records.remove(request)

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        self.calls.append("post")
        self.posts += 1
        if self.journal is not None:
            self.saw_reservation = self.journal.outstanding(intent.key) is not None
        return PostedApproval(f"posted-{self.posts}", intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        self.calls.append("commit")
        self.records.append(ApprovalRequest(intent.key, intent.action_hash, posted.message_id, posted.channel_id, created_at))


class FakeWatcher:
    def __init__(self, probe: Probe) -> None:
        self.result = probe
        self.calls: list[str] = []

    def probe(self, request: ApprovalRequest) -> Probe:
        self.calls.append("probe")
        return self.result

    def apply(self, request: ApprovalRequest, decision: Probe) -> None:
        self.calls.append(f"apply:{decision.value}")

    def drop(self, request: ApprovalRequest) -> None:
        self.calls.append("drop")


def _run(tmp_path: Path, gate: FakeGate, lease: FakeLease | None = None) -> Verdict:
    return request_owner_approval(INTENT, gate, lease or FakeLease(), PostingJournal(tmp_path / "journal"))


def test_lease_held_defers_without_touching_gate(tmp_path: Path) -> None:
    gate = FakeGate()
    verdict = _run(tmp_path, gate, FakeLease(False))
    assert (verdict.outcome, verdict.reason, gate.calls) == (Outcome.DEFERRED, Reason.LEASE_HELD, [])


@pytest.mark.parametrize("probe", [Probe.APPROVED, Probe.CANCELLED])
def test_owner_already_decided_defers_without_destroying(tmp_path: Path, probe: Probe) -> None:
    record = _request("m1")
    gate = FakeGate((record,))
    gate.probes["m1"] = [probe]
    verdict = _run(tmp_path, gate)
    assert verdict.outcome is Outcome.DEFERRED and verdict.reason is Reason.OWNER_DECIDED
    assert gate.records == [record] and gate.posts == 0


@pytest.mark.parametrize("error", [ApprovalSurfaceError("offline"), OSError("offline")])
def test_unverifiable_probe_defers(tmp_path: Path, error: ApprovalSurfaceError | OSError) -> None:
    record = _request("m1")
    gate = FakeGate((record,))
    gate.probes["m1"] = [error]
    verdict = _run(tmp_path, gate)
    assert (verdict.outcome, verdict.reason, gate.records) == (Outcome.DEFERRED, Reason.UNVERIFIABLE, [record])


def test_binding_mismatch_refuses_without_mutation(tmp_path: Path) -> None:
    record = _request("m1")
    gate = FakeGate((record,))
    gate.probes["m1"] = [Probe.BINDING_MISMATCH]
    verdict = _run(tmp_path, gate)
    assert (verdict.outcome, verdict.reason, gate.records) == (Outcome.REFUSED, Reason.BINDING_MISMATCH, [record])


def test_store_unreadable_refuses(tmp_path: Path) -> None:
    gate = FakeGate()
    gate.records_error = True
    verdict = _run(tmp_path, gate)
    assert (verdict.outcome, verdict.reason, gate.posts) == (Outcome.REFUSED, Reason.STORE_UNREADABLE, 0)


@pytest.mark.parametrize("error", [ApprovalSurfaceError("delete"), OSError("delete")])
def test_delete_failure_refuses_before_drop(tmp_path: Path, error: ApprovalSurfaceError | OSError) -> None:
    record = _request("m1")
    gate = FakeGate((record,))
    gate.delete_error = error
    verdict = _run(tmp_path, gate)
    assert (verdict.outcome, verdict.reason) == (Outcome.REFUSED, Reason.SUPERSEDE_FAILED)
    assert gate.calls[-2:] == ["probe:m1", "delete:m1"] and record in gate.records


def test_stale_posting_journal_refuses_before_store_read(tmp_path: Path) -> None:
    journal = PostingJournal(tmp_path / "journal")
    journal.reserve(KEY, "old", "2026-01-01T00:00:00Z")
    gate = FakeGate()
    verdict = request_owner_approval(INTENT, gate, FakeLease(), journal)
    assert (verdict.outcome, verdict.reason, gate.calls) == (Outcome.REFUSED, Reason.POSTING_JOURNAL_STALE, [])


def test_same_hash_single_live_is_pending(tmp_path: Path) -> None:
    record = _request("m1", "new")
    gate = FakeGate((record,))
    verdict = _run(tmp_path, gate)
    assert (verdict.outcome, verdict.live, verdict.cleared, gate.posts) == (Outcome.PENDING, record, (), 0)


def test_same_hash_duplicates_collapse_after_reprobe(tmp_path: Path) -> None:
    first = _request("m1", "new")
    second = _request("m2", "new", "2026-01-02T00:00:00Z")
    gate = FakeGate((second, first))
    verdict = _run(tmp_path, gate)
    assert verdict.outcome is Outcome.PENDING and verdict.live == first and [item.request for item in verdict.cleared] == [second]
    assert gate.calls[-3:] == ["probe:m2", "delete:m2", "drop:m2"]


def test_different_hash_posts_after_delete_then_drop(tmp_path: Path) -> None:
    record = _request("m1")
    gate = FakeGate((record,))
    verdict = _run(tmp_path, gate)
    assert verdict.outcome is Outcome.POSTED and verdict.cleared[0].reason is Reason.CONTENT_CHANGED
    assert gate.calls == ["outstanding", "probe:m1", "probe:m1", "delete:m1", "drop:m1", "post", "commit"]


def test_missing_message_drops_then_posts(tmp_path: Path) -> None:
    record = _request("m1")
    gate = FakeGate((record,))
    gate.probes["m1"] = [Probe.MISSING]
    verdict = _run(tmp_path, gate)
    assert verdict.outcome is Outcome.POSTED and verdict.cleared[0].reason is Reason.MESSAGE_MISSING
    assert "delete:m1" not in gate.calls and gate.calls.index("drop:m1") < gate.calls.index("post")


def test_empty_snapshot_posts(tmp_path: Path) -> None:
    gate = FakeGate()
    verdict = _run(tmp_path, gate)
    assert verdict.outcome is Outcome.POSTED and verdict.posted == PostedApproval("posted-1", "channel")


def test_journal_reserved_before_post_and_cleared_after_commit(tmp_path: Path) -> None:
    journal = PostingJournal(tmp_path / "journal")
    gate = FakeGate()
    gate.journal = journal
    verdict = request_owner_approval(INTENT, gate, FakeLease(), journal)
    assert verdict.outcome is Outcome.POSTED and gate.saw_reservation is True
    assert gate.calls[-2:] == ["post", "commit"] and journal.outstanding(KEY) is None


def test_verdict_is_frozen(tmp_path: Path) -> None:
    verdict = _run(tmp_path, FakeGate())
    with pytest.raises(FrozenInstanceError):
        setattr(verdict, "outcome", Outcome.REFUSED)


def test_canonical_order_uses_message_id_as_tiebreaker(tmp_path: Path) -> None:
    later = _request("z", "new")
    earlier = _request("a", "new")
    gate = FakeGate((later, earlier))
    verdict = _run(tmp_path, gate)
    assert verdict.live == earlier and gate.records == [earlier]


def test_whole_snapshot_decision_blocks_missing_cleanup(tmp_path: Path) -> None:
    missing = _request("gone")
    decided = _request("yes")
    gate = FakeGate((missing, decided))
    gate.probes = {"gone": [Probe.MISSING], "yes": [Probe.APPROVED]}
    verdict = _run(tmp_path, gate)
    assert verdict.reason is Reason.OWNER_DECIDED and not any(call.startswith(("drop", "delete")) for call in gate.calls)


def test_whole_snapshot_unverifiable_blocks_other_supersede(tmp_path: Path) -> None:
    old = _request("old")
    unknown = _request("unknown")
    gate = FakeGate((old, unknown))
    gate.probes["unknown"] = [Probe.UNVERIFIABLE]
    verdict = _run(tmp_path, gate)
    assert verdict.reason is Reason.UNVERIFIABLE and gate.records == [old, unknown]


def test_owner_reacts_on_predelete_reprobe(tmp_path: Path) -> None:
    record = _request("m1")
    gate = FakeGate((record,))
    gate.probes["m1"] = [Probe.BOUND_PENDING, Probe.APPROVED]
    verdict = _run(tmp_path, gate)
    assert verdict.reason is Reason.OWNER_DECIDED and "delete:m1" not in gate.calls


def test_message_disappears_on_predelete_reprobe(tmp_path: Path) -> None:
    record = _request("m1")
    gate = FakeGate((record,))
    gate.probes["m1"] = [Probe.BOUND_PENDING, Probe.MISSING]
    verdict = _run(tmp_path, gate)
    assert verdict.outcome is Outcome.POSTED and [item.reason for item in verdict.cleared] == [Reason.MESSAGE_MISSING]


def test_watcher_skips_when_lease_held() -> None:
    watcher = FakeWatcher(Probe.APPROVED)
    verdict = resolve_owner_decision(_request("m1"), watcher, FakeLease(False))
    assert (verdict.outcome, verdict.reason, watcher.calls) == (WatchOutcome.SKIPPED, Reason.LEASE_HELD, [])


@pytest.mark.parametrize("probe", [Probe.BOUND_PENDING, Probe.MISSING, Probe.BINDING_MISMATCH, Probe.UNVERIFIABLE])
def test_watcher_waits_without_effect(probe: Probe) -> None:
    watcher = FakeWatcher(probe)
    verdict = resolve_owner_decision(_request("m1"), watcher, FakeLease())
    assert verdict.outcome is WatchOutcome.WAITING and watcher.calls == ["probe"]


@pytest.mark.parametrize("probe", [Probe.APPROVED, Probe.CANCELLED])
def test_watcher_consumes_terminal_decision_under_lease(probe: Probe) -> None:
    watcher = FakeWatcher(probe)
    verdict = resolve_owner_decision(_request("m1"), watcher, FakeLease())
    assert verdict.outcome is WatchOutcome.CONSUMED and watcher.calls == ["probe", f"apply:{probe.value}", "drop"]
