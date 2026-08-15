from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import select
import signal
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Barrier as ProcessBarrier
from pathlib import Path
from typing import Self

import pytest

from automation.interop.approval_lease import FileKeyLease, PostingJournal, abandon, slug
from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalRecordsError,
    ApprovalRequest,
    Outcome,
    PostedApproval,
    Probe,
    Reason,
    request_owner_approval,
)

KEY = "drive:project/a"
INTENT = ApprovalIntent(KEY, "new", "channel")
type PipeEnds = tuple[int, int, int]


def _request(message: str, action_hash: str = "old", created: str = "2026-01-01T00:00:00Z") -> ApprovalRequest:
    return ApprovalRequest(KEY, action_hash, message, "channel", created)


def _data(request: ApprovalRequest) -> dict[str, str]:
    return {"key": request.key, "action_hash": request.action_hash, "message_id": request.message_id, "channel_id": request.channel_id, "created_at": request.created_at}


def _decode(data: dict[str, str]) -> ApprovalRequest:
    return ApprovalRequest(data["key"], data["action_hash"], data["message_id"], data["channel_id"], data["created_at"])


class DiskGate:
    def __init__(self, root: Path, probes: tuple[Probe, ...] = (), block_fds: tuple[int, int] | None = None) -> None:
        self.root, self.probes, self.block_fds = root, list(probes), block_fds
        self.calls: list[str] = []
        self.crash_commit = self.crash_drop = self.drop_crashed = False
        self.replace_on_drop: ApprovalRequest | None = None
        for name in ("records", "messages"):
            (root / name).mkdir(parents=True, exist_ok=True)

    def _path(self, folder: str, message: str) -> Path:
        return self.root / folder / f"{slug(message)}.json"

    def _write_record(self, path: Path, request: ApprovalRequest) -> None:
        path.write_text(json.dumps(_data(request), sort_keys=True), encoding="utf-8")

    def seed(self, request: ApprovalRequest, live: bool = True) -> Self:
        self._write_record(self._path("records", request.message_id), request)
        if live:
            self._write_message(request)
        return self

    def _write_message(self, request: ApprovalRequest) -> None:
        path = self._path("messages", request.message_id)
        data = _data(request) | {"decision": Probe.BOUND_PENDING.value}
        path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        records = self.root / "records"
        found: list[ApprovalRequest] = []
        for path in sorted(records.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                found.append(_decode(data))
            except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
                raise ApprovalRecordsError from error
        return tuple(record for record in found if record.key == key)

    def probe(self, request: ApprovalRequest) -> Probe:
        self.calls.append(f"probe:{request.message_id}")
        if self.probes:
            return self.probes.pop(0)
        try:
            data = json.loads(self._path("messages", request.message_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Probe.MISSING
        except (OSError, json.JSONDecodeError):
            return Probe.UNVERIFIABLE
        bound = (str(data.get("key")), str(data.get("action_hash")), str(data.get("message_id")),
                 str(data.get("channel_id")))
        expected = (request.key, request.action_hash, request.message_id, request.channel_id)
        return Probe(str(data.get("decision"))) if bound == expected else Probe.BINDING_MISMATCH

    def delete(self, request: ApprovalRequest) -> None:
        self.calls.append(f"delete:{request.message_id}")
        self._path("messages", request.message_id).unlink()

    def drop(self, request: ApprovalRequest) -> None:
        self.calls.append(f"drop:{request.message_id}")
        if self.crash_drop and not self.drop_crashed:
            self.drop_crashed = True
            raise SystemExit(73)
        path = self._path("records", request.message_id)
        if self.replace_on_drop is not None:
            self._write_record(path, self.replace_on_drop)
            self.replace_on_drop = None
        try:
            current = _decode(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return
        if current != request:
            raise ApprovalRecordsError
        path.unlink()

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        if self.block_fds is not None:
            ready, release = self.block_fds
            os.write(ready, b"1")
            _wait_byte(release)
        message = f"msg-{os.getpid()}-{self.post_count()}"
        posted = PostedApproval(message, intent.channel_id)
        self._write_message(ApprovalRequest(intent.key, intent.action_hash, message, intent.channel_id, "reserved"))
        log = self.root / "posts.log"
        with log.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")
        return posted

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        if self.crash_commit:
            raise SystemExit(74)
        self._write_record(self._path("records", posted.message_id), ApprovalRequest(intent.key, intent.action_hash, posted.message_id, posted.channel_id, created_at))

    def post_count(self) -> int:
        log = self.root / "posts.log"
        return len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0


def _call(base: Path, gate: DiskGate) -> tuple[Outcome, Reason | None]:
    verdict = request_owner_approval(INTENT, gate, FileKeyLease(base / "leases"), PostingJournal(base / "journal"))
    return verdict.outcome, verdict.reason


def _wait_byte(fd: int) -> bytes:
    readable, _, _ = select.select([fd], [], [], 5)
    assert readable, "pipe handshake timed out"
    return os.read(fd, 1)


def _join(process: BaseProcess) -> None:
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)
        pytest.fail("child process timed out")
    assert process.exitcode == 0


def _producer(base: Path, barrier: ProcessBarrier, pipes: PipeEnds) -> None:
    result, ready, release = pipes
    barrier.wait(timeout=5)
    outcome, reason = _call(base, DiskGate(base, block_fds=(ready, release)))
    encoded_reason = reason.value if reason is not None else "none"
    os.write(result, f"{outcome.value}|{encoded_reason}".encode())


def _holder(base: Path, ready: int, release: int) -> None:
    with FileKeyLease(base / "leases").hold(KEY) as owned:
        assert owned
        os.write(ready, b"1")
        _wait_byte(release)


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    if path.exists():
        digest.update(b"".join(item.name.encode() + item.read_bytes() for item in sorted(path.iterdir())))
    return digest.hexdigest()


def _read_result(fd: int) -> tuple[str, str | None]:
    readable, _, _ = select.select([fd], [], [], 5)
    assert readable, "result pipe timed out"
    outcome, reason = os.read(fd, 128).decode().split("|", maxsplit=1)
    return outcome, None if reason == "none" else reason


def test_two_producers_only_one_posts(tmp_path: Path) -> None:
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(2)
    (ready_r, ready_w), (release_r, release_w) = os.pipe(), os.pipe()
    result_pipes = [os.pipe(), os.pipe()]
    processes = [ctx.Process(target=_producer, args=(tmp_path, barrier, (pipe[1], ready_w, release_r))) for pipe in result_pipes]
    for process in processes:
        process.start()
    _wait_byte(ready_r)
    result_reads = [pipe[0] for pipe in result_pipes]
    available, _, _ = select.select(result_reads, [], [], 5)
    assert len(available) == 1
    first = _read_result(available[0])
    os.write(release_w, b"1")
    results = [first] + [_read_result(fd) for fd in result_reads if fd not in available]
    for process in processes:
        _join(process)
    assert sorted(results) == [("deferred", "lease-held"), ("posted", None)]
    assert len(list((tmp_path / "records").glob("*.json"))) == 1


def test_producer_defers_while_watcher_holds_lease(tmp_path: Path) -> None:
    ctx = mp.get_context("fork")
    (ready_r, ready_w), (release_r, release_w) = os.pipe(), os.pipe()
    process = ctx.Process(target=_holder, args=(tmp_path, ready_w, release_r))
    process.start()
    _wait_byte(ready_r)
    before = _tree_hash(tmp_path / "records")
    assert _call(tmp_path, DiskGate(tmp_path)) == (Outcome.DEFERRED, Reason.LEASE_HELD)
    assert _tree_hash(tmp_path / "records") == before
    os.write(release_w, b"1")
    _join(process)


def test_owner_reaction_between_probe_and_delete_defers(tmp_path: Path) -> None:
    gate = DiskGate(tmp_path, (Probe.BOUND_PENDING, Probe.APPROVED)).seed(_request("m1"))
    assert _call(tmp_path, gate) == (Outcome.DEFERRED, Reason.OWNER_DECIDED)
    assert not any(call.startswith("delete") for call in gate.calls)


def test_owner_reaction_before_first_probe_defers(tmp_path: Path) -> None:
    gate = DiskGate(tmp_path, (Probe.APPROVED,)).seed(_request("m1"))
    before = _tree_hash(tmp_path / "records")
    assert _call(tmp_path, gate) == (Outcome.DEFERRED, Reason.OWNER_DECIDED)
    assert gate.post_count() == 0 and _tree_hash(tmp_path / "records") == before


def test_binding_mismatch_neither_drops_nor_deletes(tmp_path: Path) -> None:
    record = _request("m1")
    gate = DiskGate(tmp_path, (Probe.BINDING_MISMATCH,)).seed(record)
    assert _call(tmp_path, gate) == (Outcome.REFUSED, Reason.BINDING_MISMATCH)
    assert gate.outstanding(KEY) == (record,) and gate.calls == ["probe:m1"]


def test_corrupt_record_refuses_instead_of_reposting(tmp_path: Path) -> None:
    records = tmp_path / "records"
    records.mkdir(exist_ok=True)
    (records / "broken.json").write_text("{", encoding="utf-8")
    gate = DiskGate(tmp_path)
    assert _call(tmp_path, gate) == (Outcome.REFUSED, Reason.STORE_UNREADABLE)
    assert gate.post_count() == 0


def test_crash_after_post_wedges_key_until_reconciled(tmp_path: Path) -> None:
    gate = DiskGate(tmp_path)
    gate.crash_commit = True
    with pytest.raises(SystemExit):
        request_owner_approval(INTENT, gate, FileKeyLease(tmp_path / "leases"), PostingJournal(tmp_path / "journal"))
    assert _call(tmp_path, DiskGate(tmp_path)) == (Outcome.REFUSED, Reason.POSTING_JOURNAL_STALE)
    assert gate.post_count() == 1


def test_abandon_clears_the_posting_journal(tmp_path: Path) -> None:
    journal = PostingJournal(tmp_path / "journal")
    journal.reserve(KEY, "new", "2026-01-01T00:00:00Z")
    audit = tmp_path / "audit.jsonl"
    abandon(KEY, journal, audit)
    assert journal.outstanding(KEY) is None and KEY in audit.read_text(encoding="utf-8")
    assert _call(tmp_path, DiskGate(tmp_path))[0] is Outcome.POSTED


def test_crash_between_delete_and_drop_self_heals(tmp_path: Path) -> None:
    gate = DiskGate(tmp_path).seed(_request("m1"))
    gate.crash_drop = True
    with pytest.raises(SystemExit):
        _call(tmp_path, gate)
    assert _call(tmp_path, gate)[0] is Outcome.POSTED
    assert gate.post_count() == 1 and len(gate.outstanding(KEY)) == 1


def test_sigkill_holder_does_not_wedge_the_key(tmp_path: Path) -> None:
    ctx = mp.get_context("fork")
    (ready_r, ready_w), (release_r, _) = os.pipe(), os.pipe()
    process = ctx.Process(target=_holder, args=(tmp_path, ready_w, release_r))
    process.start()
    _wait_byte(ready_r)
    assert process.pid is not None
    os.kill(process.pid, signal.SIGKILL)
    process.join(timeout=5)
    assert not process.is_alive()
    assert _call(tmp_path, DiskGate(tmp_path))[0] is Outcome.POSTED
    assert (tmp_path / "leases" / f"{slug(KEY)}.lease").exists()


def test_drop_is_cas_and_ignores_a_replaced_record(tmp_path: Path) -> None:
    original, replacement = _request("m1"), _request("m2")
    gate = DiskGate(tmp_path).seed(original, live=False)
    gate.replace_on_drop = replacement
    with pytest.raises(ApprovalRecordsError):
        _call(tmp_path, gate)
    assert gate.outstanding(KEY) == (replacement,) and gate.post_count() == 0


def test_pre_existing_duplicates_collapse_to_one(tmp_path: Path) -> None:
    oldest = _request("m1", "new")
    middle = _request("m2", "new", "2026-01-02T00:00:00Z")
    newest = _request("m3", "new", "2026-01-03T00:00:00Z")
    gate = DiskGate(tmp_path)
    for record in (newest, oldest, middle):
        gate.seed(record)
    assert _call(tmp_path, gate)[0] is Outcome.PENDING
    assert gate.outstanding(KEY) == (oldest,) and gate.calls[-6:] == ["probe:m2", "delete:m2", "drop:m2", "probe:m3", "delete:m3", "drop:m3"]
