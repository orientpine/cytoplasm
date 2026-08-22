"""All 31 A2.2 acceptance contracts in the plan-mandated module (# noqa: SIZE_OK)."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import subprocess
import threading
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from automation.interop.report import ReportStatus, TaskReport, format_report, mask_summary
from automation.repair import (
    repair_capability,
    repair_report_consumer,
    repair_report_queue,
    repair_report_send,
)

_REAL_CARD_STATE = repair_report_consumer.card_state

type JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class Sandbox:
    home: Path
    queue: Path
    ack: Path
    registry: Path


@pytest.fixture(autouse=True)
def sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Sandbox:
    home = tmp_path / "home"
    queue = tmp_path / "queue"
    ack = tmp_path / "ack"
    capability = tmp_path / "capability"
    registry = tmp_path / "repair-tickets.json"
    for directory in (home, queue, ack, capability):
        directory.mkdir()
    (queue / "queue.lock").touch(mode=0o640)
    registry.write_text(
        json.dumps({"signature": {"ticket_id": "t_consumer", "occurrences": 99}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("REPAIR_REPORT_QUEUE", str(queue))
    monkeypatch.setenv("REPAIR_REPORT_ACK", str(ack))
    monkeypatch.setenv("REPAIR_CAPABILITY_DIR", str(capability))
    monkeypatch.setenv("REPAIR_STATE_FILE", str(registry))
    monkeypatch.setattr(
        repair_report_consumer,
        "card_state",
        lambda _ticket_id: repair_report_consumer.CardState(status="done", blocked_kind=None),
        raising=False,
    )
    monkeypatch.setattr(repair_report_consumer, "channel_watermark", lambda **_kwargs: "100", raising=False)
    monkeypatch.setattr(repair_report_consumer, "send_report", lambda *_args, **_kwargs: "1234", raising=False)
    monkeypatch.setattr(
        repair_report_consumer,
        "find_report",
        lambda **_kwargs: (True, "101", True),
        raising=False,
    )
    return Sandbox(home=home, queue=queue, ack=ack, registry=registry)


def _request(
    request_id: str = "1" * 32,
    *,
    occurrence: str = "1",
    ticket_id: str = "t_consumer",
    operation: str = "complete",
    reason_code: str = "applied",
) -> repair_report_queue.ReportRequest:
    return repair_report_queue.ReportRequest(
        request_id=request_id,
        operation=operation,
        ticket_id=ticket_id,
        reason_code=reason_code,
        occurrence=occurrence,
        mac=repair_capability.mac(ticket_id, occurrence),
        created="2026-08-07T12:00:00+00:00",
    )


def _raw(request: repair_report_queue.ReportRequest) -> bytes:
    return json.dumps(asdict(request), sort_keys=True, separators=(",", ":")).encode() + b"\n"


def _enqueue(sandbox: Sandbox, *requests: repair_report_queue.ReportRequest) -> None:
    (sandbox.queue / "pending.jsonl").write_bytes(b"".join(_raw(request) for request in requests))


def _json(path: Path) -> dict[str, JsonValue]:
    payload: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _state_path(sandbox: Sandbox) -> Path:
    return sandbox.home / ".hermes" / "repair-report-consumer" / "state.json"


def _record(
    request: repair_report_queue.ReportRequest,
    *,
    transition: str = "pending",
    report: str = "pending",
    terminal_reason: str = "",
    terminal_at: str = "",
) -> dict[str, str | int]:
    raw = _raw(request)
    return {
        "ticket_id": request.ticket_id,
        "operation": request.operation,
        "reason_code": request.reason_code,
        "occurrence": request.occurrence,
        "line_digest": repair_report_queue.line_digest(raw),
        "semantic_key": repair_report_queue.semantic_key(request),
        "transition": transition,
        "report": report,
        "transition_attempts": 0,
        "report_attempts": 0,
        "reconcile_attempts": 0,
        "terminal_reason": terminal_reason,
        "report_timestamp": "",
        "watermark_message_id": "",
        "reconcile_cursor": "",
        "reconcile_upper": "",
        "message_id_last4": "",
        "terminal_at": terminal_at,
        "last_warning_at": "",
    }


def _write_state(
    sandbox: Sandbox,
    records: dict[str, dict[str, str | int]],
    reservations: dict[str, str],
) -> None:
    path = _state_path(sandbox)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"records": records, "reservations": reservations, "last_timestamp": ""}),
        encoding="utf-8",
    )


def test_01_complete_writes_ok_ack_and_semantic_receipt(sandbox: Sandbox) -> None:
    request = _request()
    _enqueue(sandbox, request)

    completed = repair_report_consumer.consume_once()

    ack = _json(sandbox.ack / f"{request.request_id}.json")
    assert completed == 1
    assert ack["terminal_reason"] == "ok"
    assert ack["report_timestamp"] and ack["watermark_message_id"]
    assert (sandbox.ack / f"sem-{repair_report_queue.semantic_key(request)}.json").is_file()


@pytest.mark.parametrize("raw", [b"x" * 513 + b"\n", b"\xff\xfe\n"])
def test_02_invalid_bytes_write_digest_receipt_without_execution(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
) -> None:
    (sandbox.queue / "pending.jsonl").write_bytes(raw)
    monkeypatch.setattr(
        repair_report_consumer,
        "_execute_transition_and_report",
        lambda *_args: pytest.fail("invalid input reached execution"),
    )

    assert repair_report_consumer.consume_once() == 1
    assert (sandbox.ack / f"invalid-{repair_report_queue.line_digest(raw)}.json").is_file()


def test_03_oversized_queue_is_rejected_once_without_side_effects(
    sandbox: Sandbox,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (sandbox.queue / "pending.jsonl").write_bytes(b"x" * (8 * 1024 * 1024 + 1))

    assert repair_report_consumer.consume_once() == 0
    assert capsys.readouterr().err.count("warning") == 1
    assert list(sandbox.ack.iterdir()) == []


def test_04_pending_symlink_is_a_no_op(sandbox: Sandbox) -> None:
    (sandbox.queue / "pending.jsonl").symlink_to(sandbox.registry)

    assert repair_report_consumer.consume_once() == 0
    assert list(sandbox.ack.iterdir()) == []


def test_05_fifo_without_writer_returns_and_releases_both_locks(sandbox: Sandbox) -> None:
    os.mkfifo(sandbox.queue / "pending.jsonl")

    worker = threading.Thread(target=repair_report_consumer.consume_once, daemon=True)
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive()
    for path in (
        sandbox.queue / "queue.lock",
        sandbox.home / ".hermes" / "repair-report-consumer" / "state.json.lock",
    ):
        with path.open("rb") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_06_partial_final_line_waits_for_a_later_tick(sandbox: Sandbox) -> None:
    request = _request()
    pending = sandbox.queue / "pending.jsonl"
    pending.write_bytes(_raw(request).removesuffix(b"\n"))

    first = repair_report_consumer.consume_once()
    with pending.open("ab") as handle:
        handle.write(b"\n")
    second = repair_report_consumer.consume_once()

    assert (first, second) == (0, 1)


def test_07_terminal_prefix_does_not_starve_actionable_tail(sandbox: Sandbox) -> None:
    terminal = _request("1" * 32)
    actionable = _request("2" * 32, occurrence="2")
    terminal_raw = _raw(terminal)
    (sandbox.ack / f"{terminal.request_id}.json").write_text(
        json.dumps(
            {
                "line_digest": repair_report_queue.line_digest(terminal_raw),
                "semantic_key": repair_report_queue.semantic_key(terminal),
                "terminal_at": "2026-08-07T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (sandbox.queue / "pending.jsonl").write_bytes(terminal_raw * 6000 + _raw(actionable))

    assert repair_report_consumer.consume_once() == 1
    assert (sandbox.ack / f"{actionable.request_id}.json").is_file()


def test_08_matching_ack_skips_execution_after_state_loss(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    raw = _raw(request)
    _enqueue(sandbox, request)
    (sandbox.ack / f"{request.request_id}.json").write_text(
        json.dumps(
            {
                "line_digest": repair_report_queue.line_digest(raw),
                "semantic_key": repair_report_queue.semantic_key(request),
                "terminal_at": "2026-08-07T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        repair_report_consumer,
        "_execute_transition_and_report",
        lambda *_args: pytest.fail("matching ACK was re-executed"),
    )

    assert repair_report_consumer.consume_once() == 0


def test_09_ack_conflict_wins_even_when_state_identity_matches(sandbox: Sandbox) -> None:
    request = _request()
    raw = _raw(request)
    key = repair_report_queue.semantic_key(request)
    _enqueue(sandbox, request)
    _write_state(sandbox, {request.request_id: _record(request)}, {key: request.request_id})
    original_ack = sandbox.ack / f"{request.request_id}.json"
    original_ack.write_text(
        json.dumps({"line_digest": "f" * 64, "semantic_key": key, "terminal_at": "x"}),
        encoding="utf-8",
    )
    before = original_ack.read_bytes()

    assert repair_report_consumer.consume_once() == 1
    assert (sandbox.ack / f"conflict-{repair_report_queue.line_digest(raw)}.json").is_file()
    assert original_ack.read_bytes() == before


def test_10_ack_detects_reused_request_id_after_state_loss(sandbox: Sandbox) -> None:
    old = _request()
    reused = replace(old, occurrence="2", mac=repair_capability.mac(old.ticket_id, "2"))
    _enqueue(sandbox, reused)
    (sandbox.ack / f"{old.request_id}.json").write_text(
        json.dumps(
            {
                "line_digest": repair_report_queue.line_digest(_raw(old)),
                "semantic_key": repair_report_queue.semantic_key(old),
                "terminal_at": "x",
            }
        ),
        encoding="utf-8",
    )

    assert repair_report_consumer.consume_once() == 1
    digest = repair_report_queue.line_digest(_raw(reused))
    assert (sandbox.ack / f"conflict-{digest}.json").is_file()


def test_11_existing_semantic_receipt_dead_letters_new_uuid(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request("3" * 32)
    key = repair_report_queue.semantic_key(request)
    _enqueue(sandbox, request)
    (sandbox.ack / f"sem-{key}.json").write_text(json.dumps({"terminal_at": "x"}), encoding="utf-8")
    monkeypatch.setattr(
        repair_report_consumer,
        "_execute_transition_and_report",
        lambda *_args: pytest.fail("duplicate semantic request was executed"),
    )

    assert repair_report_consumer.consume_once() == 1
    assert _json(sandbox.ack / f"{request.request_id}.json")["terminal_reason"] == "duplicate_semantic"


def test_12_forged_mac_cannot_reserve_or_block_legitimate_request(sandbox: Sandbox) -> None:
    legitimate = _request("5" * 32)
    forged = replace(legitimate, request_id="4" * 32, mac="0" * 64)
    _enqueue(sandbox, forged, legitimate)

    assert repair_report_consumer.consume_once() == 2
    state = _json(_state_path(sandbox))
    reservations = state["reservations"]
    assert isinstance(reservations, dict)
    assert reservations[repair_report_queue.semantic_key(legitimate)] == legitimate.request_id
    assert _json(sandbox.ack / f"{forged.request_id}.json")["terminal_reason"] == "bad_capability"


def test_13_existing_semantic_receipt_is_immutable(sandbox: Sandbox) -> None:
    request = _request()
    key = repair_report_queue.semantic_key(request)
    path = sandbox.ack / f"sem-{key}.json"
    path.write_text(json.dumps({"terminal_at": "old", "sentinel": "keep"}), encoding="utf-8")
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    _enqueue(sandbox, request)

    repair_report_consumer.consume_once()

    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_14_duplicate_uuids_defer_while_reservation_owner_is_in_flight(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _request("6" * 32)
    key = repair_report_queue.semantic_key(owner)
    duplicates = [replace(owner, request_id=f"{index:032x}") for index in range(7, 12)]
    _write_state(
        sandbox,
        {owner.request_id: _record(owner, transition="done", report="in_flight")},
        {key: owner.request_id},
    )
    _enqueue(sandbox, *duplicates)
    monkeypatch.setattr(repair_report_consumer, "find_report", lambda **_kwargs: (False, "101", False))

    assert repair_report_consumer.consume_once() == 0
    assert list(sandbox.ack.iterdir()) == []


def test_15_deferred_duplicates_finish_after_owner_terminalizes(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _request("6" * 32)
    key = repair_report_queue.semantic_key(owner)
    duplicate = replace(owner, request_id="7" * 32)
    _write_state(
        sandbox,
        {owner.request_id: _record(owner, transition="done", report="in_flight")},
        {key: owner.request_id},
    )
    _enqueue(sandbox, duplicate)
    monkeypatch.setattr(repair_report_consumer, "find_report", lambda **_kwargs: (False, "101", False))
    assert repair_report_consumer.consume_once() == 0
    monkeypatch.setattr(repair_report_consumer, "find_report", lambda **_kwargs: (True, "101", True))

    assert repair_report_consumer.consume_once() == 2
    assert _json(sandbox.ack / f"{duplicate.request_id}.json")["terminal_reason"] == "duplicate_semantic"


def test_16_canonical_copy_recovers_in_flight_record_without_queue(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request("8" * 32)
    _enqueue(sandbox, request)
    real_execute = repair_report_consumer._execute_transition_and_report

    def pause(
        state: repair_report_consumer.ConsumerState,
        request_id: str,
        _budgets: repair_report_consumer.TickBudgets,
    ) -> repair_report_consumer.ConsumerState:
        record = replace(state.records[request_id], transition="done", report="in_flight")
        return replace(state, records=state.records | {request_id: record})

    monkeypatch.setattr(repair_report_consumer, "_execute_transition_and_report", pause)
    assert repair_report_consumer.consume_once() == 0
    stored = _json(_state_path(sandbox))["records"]
    assert isinstance(stored, dict)
    stored_record = stored[request.request_id]
    assert isinstance(stored_record, dict)
    assert [stored_record[field] for field in ("ticket_id", "operation", "reason_code", "occurrence")] == [
        request.ticket_id,
        request.operation,
        request.reason_code,
        request.occurrence,
    ]
    (sandbox.queue / "pending.jsonl").unlink()
    monkeypatch.setattr(repair_report_consumer, "_execute_transition_and_report", real_execute)

    assert repair_report_consumer.consume_once() == 1
    assert (sandbox.ack / f"{request.request_id}.json").is_file()


def test_17_report_marker_uses_occurrence_independent_key_and_content(sandbox: Sandbox) -> None:
    request = _request()
    _enqueue(sandbox, request)

    repair_report_consumer.consume_once()

    report_key = hashlib.sha256(f"{request.ticket_id}|{request.operation}|{request.reason_code}".encode()).hexdigest()
    marker = _json(sandbox.ack / f"reported-{report_key}.json")
    assert set(marker) == {"ticket_id", "operation", "reason_code", "first_reported_at"}


def test_18_terminal_files_are_created_marker_then_semantic_then_ack(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _enqueue(sandbox, request)
    real_write = repair_report_consumer._write_atomic
    order: list[str] = []

    def observe(path: Path, payload: bytes, *, immutable: bool = False) -> bool:
        if path.parent == sandbox.ack:
            order.append(path.name)
        return real_write(path, payload, immutable=immutable)

    monkeypatch.setattr(repair_report_consumer, "_write_atomic", observe)

    repair_report_consumer.consume_once()

    assert order[0].startswith("reported-")
    assert order[1].startswith("sem-")
    assert order[2] == f"{request.request_id}.json"


def test_19_marker_failure_retries_receipts_without_reexecution(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _enqueue(sandbox, request)
    real_write = repair_report_consumer._write_atomic
    failed = False

    def fail_marker(path: Path, payload: bytes, *, immutable: bool = False) -> bool:
        nonlocal failed
        if path.name.startswith("reported-") and not failed:
            failed = True
            raise OSError("injected marker failure")
        return real_write(path, payload, immutable=immutable)

    monkeypatch.setattr(repair_report_consumer, "_write_atomic", fail_marker)
    assert repair_report_consumer.consume_once() == 0
    assert not (sandbox.ack / f"sem-{repair_report_queue.semantic_key(request)}.json").exists()
    assert not (sandbox.ack / f"{request.request_id}.json").exists()
    monkeypatch.setattr(repair_report_consumer, "_write_atomic", real_write)
    monkeypatch.setattr(
        repair_report_consumer,
        "_execute_transition_and_report",
        lambda *_args: pytest.fail("terminal record was re-executed"),
    )

    assert repair_report_consumer.consume_once() == 1


def test_20_existing_report_marker_is_immutable(sandbox: Sandbox) -> None:
    request = _request()
    report_key = hashlib.sha256(f"{request.ticket_id}|{request.operation}|{request.reason_code}".encode()).hexdigest()
    marker = sandbox.ack / f"reported-{report_key}.json"
    marker.write_text('{"sentinel":"keep"}', encoding="utf-8")
    before = (marker.read_bytes(), marker.stat().st_mtime_ns)
    _enqueue(sandbox, request)

    repair_report_consumer.consume_once()

    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before


def test_21_dead_report_writes_no_reported_marker(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _enqueue(sandbox, request)

    def dead(
        state: repair_report_consumer.ConsumerState,
        request_id: str,
        _budgets: repair_report_consumer.TickBudgets,
    ) -> repair_report_consumer.ConsumerState:
        record = replace(
            state.records[request_id],
            transition="dead",
            report="dead",
            terminal_reason="transition_exhausted",
            terminal_at=datetime.now(tz=UTC).isoformat(),
        )
        return replace(state, records=state.records | {request_id: record})

    monkeypatch.setattr(repair_report_consumer, "_execute_transition_and_report", dead)

    assert repair_report_consumer.consume_once() == 1
    assert not list(sandbox.ack.glob("reported-*.json"))


def test_22_unknown_ticket_is_acknowledged_without_semantic_receipt(sandbox: Sandbox) -> None:
    request = _request(ticket_id="t_unknown")
    _enqueue(sandbox, request)

    assert repair_report_consumer.consume_once() == 1
    assert _json(sandbox.ack / f"{request.request_id}.json")["terminal_reason"] == "unknown_ticket"
    assert not list(sandbox.ack.glob("sem-*.json"))


def test_23_mutated_mac_is_rejected_without_execution(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    request = replace(request, mac=("0" if request.mac[0] != "0" else "1") + request.mac[1:])
    _enqueue(sandbox, request)
    monkeypatch.setattr(
        repair_report_consumer,
        "_execute_transition_and_report",
        lambda *_args: pytest.fail("bad capability reached execution"),
    )

    assert repair_report_consumer.consume_once() == 1
    assert _json(sandbox.ack / f"{request.request_id}.json")["terminal_reason"] == "bad_capability"


def test_24_tick_processes_at_most_twenty_actionable_requests(sandbox: Sandbox) -> None:
    requests = [_request(f"{index:032x}", occurrence=str(index)) for index in range(1, 22)]
    _enqueue(sandbox, *requests)

    assert repair_report_consumer.consume_once() == 20
    assert len(list(sandbox.ack.glob("[0-9a-f]*.json"))) == 20


def test_25_corrupt_state_fails_closed_without_side_effects(sandbox: Sandbox) -> None:
    path = _state_path(sandbox)
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    _enqueue(sandbox, _request())

    assert repair_report_consumer.consume_once() == 0
    assert list(sandbox.ack.iterdir()) == []


def test_26_semantic_receipt_without_ack_recovers_as_duplicate(sandbox: Sandbox) -> None:
    request = _request()
    key = repair_report_queue.semantic_key(request)
    (sandbox.ack / f"sem-{key}.json").write_text(json.dumps({"terminal_at": "x"}), encoding="utf-8")
    _enqueue(sandbox, request)

    assert repair_report_consumer.consume_once() == 1
    assert _json(sandbox.ack / f"{request.request_id}.json")["terminal_reason"] == "duplicate_semantic"


def test_27_parallel_consumers_are_serialized_by_state_lock(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    entries: list[int] = []
    real_snapshot = repair_report_consumer._snapshot_lines

    def gated() -> list[bytes] | None:
        entries.append(1)
        if len(entries) == 1:
            entered.set()
            assert release.wait(timeout=3)
        return real_snapshot()

    monkeypatch.setattr(repair_report_consumer, "_snapshot_lines", gated)
    first = threading.Thread(target=repair_report_consumer.consume_once)
    second = threading.Thread(target=repair_report_consumer.consume_once)
    first.start()
    assert entered.wait(timeout=3)
    second.start()
    assert entries == [1]
    release.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert entries == [1, 1]


def test_28_queue_lock_is_acquired_shared_only(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enqueue(sandbox, _request())
    operations: list[int] = []
    real_flock = repair_report_consumer.fcntl.flock

    def observe(descriptor: int, operation: int) -> None:
        operations.append(operation)
        real_flock(descriptor, operation)

    monkeypatch.setattr(repair_report_consumer.fcntl, "flock", observe)

    repair_report_consumer.consume_once()

    assert operations[:2] == [fcntl.LOCK_EX, fcntl.LOCK_SH]


def test_29_receipt_free_text_contains_no_forbidden_detail(sandbox: Sandbox) -> None:
    request = _request()
    _enqueue(sandbox, request)
    repair_report_consumer.consume_once()
    excluded = {"watermark_message_id", "message_id_last4"}

    for path in sandbox.ack.glob("*.json"):
        payload = _json(path)
        for key, value in payload.items():
            if key not in excluded and isinstance(value, str):
                assert mask_summary(value) == value


def test_30_receipts_and_marker_have_mode_0640(sandbox: Sandbox) -> None:
    request = _request()
    _enqueue(sandbox, request)

    repair_report_consumer.consume_once()

    generated = [*sandbox.ack.glob("*.json"), _state_path(sandbox)]
    assert {stat.S_IMODE(path.stat().st_mode) for path in generated} == {0o640}


def test_31_all_runtime_paths_are_inside_the_sandbox(sandbox: Sandbox) -> None:
    request = _request()
    _enqueue(sandbox, request)

    repair_report_consumer.consume_once()

    generated = [*sandbox.ack.glob("*.json"), _state_path(sandbox)]
    assert generated
    assert all(path.is_relative_to(sandbox.home.parent) for path in generated)


def test_paused_record_writes_no_receipts_and_is_not_garbage_collected(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    key = repair_report_queue.semantic_key(request)
    old = (datetime.now(tz=UTC) - timedelta(days=31)).isoformat()
    _write_state(
        sandbox,
        {request.request_id: _record(request, transition="done", report="paused", terminal_at=old)},
        {key: request.request_id},
    )
    monkeypatch.setattr(repair_report_consumer, "find_report", lambda **_kwargs: (False, "101", False))

    assert repair_report_consumer.consume_once() == 0
    records = _json(_state_path(sandbox))["records"]
    assert isinstance(records, dict)
    assert request.request_id in records
    assert list(sandbox.ack.iterdir()) == []


def test_terminal_record_is_gc_eligible_only_after_thirty_days_and_both_receipts(
    sandbox: Sandbox,
) -> None:
    request = _request()
    key = repair_report_queue.semantic_key(request)
    old = (datetime.now(tz=UTC) - timedelta(days=31)).isoformat()
    record = _record(request, transition="done", report="dead", terminal_reason="ok", terminal_at=old)
    _write_state(sandbox, {request.request_id: record}, {key: request.request_id})
    (sandbox.ack / f"sem-{key}.json").write_text(json.dumps({"terminal_at": old}), encoding="utf-8")
    (sandbox.ack / f"{request.request_id}.json").write_text(json.dumps({"terminal_at": old}), encoding="utf-8")

    repair_report_consumer.consume_once()

    records = _json(_state_path(sandbox))["records"]
    assert isinstance(records, dict)
    assert request.request_id not in records


def _saved_record(sandbox: Sandbox, request_id: str) -> dict[str, JsonValue]:
    state = _json(_state_path(sandbox))
    records = state["records"]
    assert isinstance(records, dict)
    record = records[request_id]
    assert isinstance(record, dict)
    return record


def _seed(
    sandbox: Sandbox,
    request: repair_report_queue.ReportRequest,
    **changes: str | int,
) -> None:
    record = _record(request)
    record.update(changes)
    key = repair_report_queue.semantic_key(request)
    _write_state(sandbox, {request.request_id: record}, {key: request.request_id})


def _card(status: str, kind: str | None = None) -> repair_report_consumer.CardState:
    return repair_report_consumer.CardState(status=status, blocked_kind=kind)


def test_transition_already_target_sends_report_without_command(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _enqueue(sandbox, request)
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(repair_report_consumer, "_run_kanban", lambda *args: commands.append(args))

    assert repair_report_consumer.consume_once() == 1

    assert commands == []
    assert _saved_record(sandbox, request.request_id)["report"] == "done"


def test_transition_complete_runs_correct_flag_then_confirms_readback(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _enqueue(sandbox, request)
    states = iter((_card("ready"), _card("done")))
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(repair_report_consumer, "card_state", lambda _ticket: next(states))
    monkeypatch.setattr(repair_report_consumer, "_run_kanban", lambda *args: commands.append(args))

    assert repair_report_consumer.consume_once() == 1

    assert commands == [("complete", request.ticket_id, "--result", repair_report_consumer.COMPLETE_RESULT)]


def test_transition_reopen_unblocked_runs_only_block(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(operation="reopen", reason_code="unspecified")
    _enqueue(sandbox, request)
    states = iter((_card("ready"), _card("blocked", "needs_input")))
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(repair_report_consumer, "card_state", lambda _ticket: next(states))
    monkeypatch.setattr(repair_report_consumer, "_run_kanban", lambda *args: commands.append(args))

    repair_report_consumer.consume_once()

    assert [command[0] for command in commands] == ["block"]


def test_transition_reopen_other_kind_unblocks_then_blocks(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(operation="reopen", reason_code="bank_red")
    _enqueue(sandbox, request)
    states = iter((_card("blocked", "transient"), _card("blocked", "needs_input")))
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(repair_report_consumer, "card_state", lambda _ticket: next(states))
    monkeypatch.setattr(repair_report_consumer, "_run_kanban", lambda *args: commands.append(args))

    repair_report_consumer.consume_once()

    assert [command[0] for command in commands] == ["unblock", "block"]


def test_transition_reopen_after_unblock_crash_runs_only_block(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(operation="reopen", reason_code="approval_expired")
    _seed(sandbox, request, transition="in_flight")
    states = iter((_card("ready"), _card("blocked", "needs_input")))
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(repair_report_consumer, "card_state", lambda _ticket: next(states))
    monkeypatch.setattr(repair_report_consumer, "_run_kanban", lambda *args: commands.append(args))

    repair_report_consumer.consume_once()

    assert [command[0] for command in commands] == ["block"]


def test_transition_readback_not_target_returns_pending_and_increments_only_transition(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _enqueue(sandbox, request)
    monkeypatch.setattr(repair_report_consumer, "card_state", lambda _ticket: _card("ready"))
    monkeypatch.setattr(repair_report_consumer, "_run_kanban", lambda *_args: None)

    assert repair_report_consumer.consume_once() == 0

    record = _saved_record(sandbox, request.request_id)
    assert (record["transition"], record["transition_attempts"], record["report_attempts"]) == (
        "pending",
        1,
        0,
    )


def test_transition_in_flight_restart_readback_target_runs_no_command(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _seed(sandbox, request, transition="in_flight")
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(repair_report_consumer, "_run_kanban", lambda *args: commands.append(args))

    assert repair_report_consumer.consume_once() == 1

    assert commands == []


def test_transition_dead_never_sends_or_writes_report_marker(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _seed(sandbox, request, transition="dead", report="pending", terminal_reason="transition_exhausted")
    sends: list[int] = []
    monkeypatch.setattr(repair_report_consumer, "send_report", lambda *_args, **_kwargs: sends.append(1))

    assert repair_report_consumer.consume_once() == 0

    assert sends == []
    assert not list(sandbox.ack.glob("reported-*.json"))


def test_transition_attempt_eleven_atomically_deads_report_and_writes_receipts(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _seed(sandbox, request, transition_attempts=10)
    monkeypatch.setattr(repair_report_consumer, "card_state", lambda _ticket: _card("ready"))
    monkeypatch.setattr(repair_report_consumer, "_run_kanban", lambda *_args: None)
    real_save = repair_report_consumer._save_state
    dead_snapshots: list[tuple[str, str]] = []

    def observe(state: repair_report_consumer.ConsumerState) -> None:
        record = state.records[request.request_id]
        if record.transition == "dead":
            dead_snapshots.append((record.transition, record.report))
        real_save(state)

    monkeypatch.setattr(repair_report_consumer, "_save_state", observe)

    assert repair_report_consumer.consume_once() == 1

    assert dead_snapshots == [("dead", "dead")]
    assert (sandbox.ack / f"sem-{repair_report_queue.semantic_key(request)}.json").is_file()
    assert (sandbox.ack / f"{request.request_id}.json").is_file()
    assert repair_report_consumer.consume_once() == 0


def test_reconcile_found_completes_without_resend(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _seed(
        sandbox,
        request,
        transition="done",
        report="in_flight",
        report_timestamp="2026-08-07T12:00:00+00:00",
        watermark_message_id="100",
        reconcile_upper="200",
    )
    sends: list[int] = []
    monkeypatch.setattr(repair_report_consumer, "send_report", lambda *_args, **_kwargs: sends.append(1))

    assert repair_report_consumer.consume_once() == 1

    assert sends == []
    assert _saved_record(sandbox, request.request_id)["report"] == "done"


def test_reconcile_exhausted_resends_with_new_identity_and_resets_window(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    old_timestamp = "2026-08-07T12:00:00+00:00"
    _seed(
        sandbox,
        request,
        transition="done",
        report="in_flight",
        report_timestamp=old_timestamp,
        watermark_message_id="100",
        reconcile_upper="200",
        reconcile_cursor="150",
    )
    monkeypatch.setattr(repair_report_consumer, "find_report", lambda **_kwargs: (False, "101", True))
    monkeypatch.setattr(repair_report_consumer, "channel_watermark", lambda **_kwargs: "300")

    repair_report_consumer.consume_once()

    record = _saved_record(sandbox, request.request_id)
    assert record["watermark_message_id"] == "300"
    assert record["report_timestamp"] != old_timestamp
    assert (record["reconcile_upper"], record["reconcile_cursor"], record["report_attempts"]) == ("", "", 1)


def test_reconcile_unexhausted_persists_cursor_without_resend(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _seed(
        sandbox,
        request,
        transition="done",
        report="in_flight",
        report_timestamp="2026-08-07T12:00:00+00:00",
        watermark_message_id="100",
        reconcile_upper="300",
    )
    sends: list[int] = []
    monkeypatch.setattr(repair_report_consumer, "find_report", lambda **_kwargs: (False, "200", False))
    monkeypatch.setattr(repair_report_consumer, "send_report", lambda *_args, **_kwargs: sends.append(1))

    repair_report_consumer.consume_once()

    record = _saved_record(sandbox, request.request_id)
    assert (record["report"], record["reconcile_cursor"], record["reconcile_attempts"]) == (
        "in_flight",
        "200",
        0,
    )
    assert sends == []


def _run_reconcile_backlog(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, JsonValue]]:
    request = _request()
    timestamp = "2026-08-07T12:00:00+00:00"
    _seed(
        sandbox,
        request,
        transition="done",
        report="in_flight",
        report_timestamp=timestamp,
        watermark_message_id="100",
    )
    monkeypatch.setattr(
        repair_report_send,
        "load_config",
        lambda: {"agent_id": "agent-test", "agents_log_channel_id": "channel-test"},
    )
    monkeypatch.setattr(repair_report_send, "_bot_user_id_cache", None)
    monkeypatch.setattr(repair_report_consumer, "find_report", repair_report_send.find_report)
    monkeypatch.setattr(repair_report_consumer, "channel_watermark", repair_report_send.channel_watermark)
    target = format_report(
        TaskReport("agent-test", request.ticket_id, ReportStatus.DONE, "ok", (), datetime.fromisoformat(timestamp))
    )

    def fetch(path: str) -> JsonValue:
        if path == "/users/@me":
            return {"id": "999"}
        if path.endswith("messages?limit=1"):
            return [{"id": "220"}]
        before = int(path.split("before=", 1)[1].split("&", 1)[0])
        identifier = before - 1
        if identifier <= 100:
            return []
        content = target if identifier == 101 else "not-a-report"
        return [{"id": str(identifier), "author": {"id": "999"}, "content": content}]

    monkeypatch.setattr(repair_report_consumer, "_discord_fetch", fetch)
    snapshots: list[dict[str, JsonValue]] = []
    for _ in range(3):
        repair_report_consumer.consume_once()
        snapshots.append(_saved_record(sandbox, request.request_id))
    return snapshots


def test_reconcile_backlog_descends_across_three_ticks_to_target(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = _run_reconcile_backlog(sandbox, monkeypatch)

    assert [snapshot["reconcile_cursor"] for snapshot in snapshots[:2]] == ["172", "122"]
    assert snapshots[2]["report"] == "done"


def test_reconcile_backlog_progress_does_not_increment_attempts_or_pause(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = _run_reconcile_backlog(sandbox, monkeypatch)

    assert [snapshot["reconcile_attempts"] for snapshot in snapshots] == [0, 0, 0]
    assert all(snapshot["report"] != "paused" for snapshot in snapshots)


def test_reconcile_lookup_exception_keeps_in_flight_and_increments_attempt(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _seed(
        sandbox,
        request,
        transition="done",
        report="in_flight",
        report_timestamp="2026-08-07T12:00:00+00:00",
        watermark_message_id="100",
        reconcile_upper="200",
    )
    monkeypatch.setattr(
        repair_report_consumer,
        "find_report",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("lookup failed")),
    )

    repair_report_consumer.consume_once()

    record = _saved_record(sandbox, request.request_id)
    assert (record["report"], record["reconcile_attempts"]) == ("in_flight", 1)


def test_reconcile_attempt_eleven_pauses_without_receipts_and_warns_once(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _request()
    _seed(
        sandbox,
        request,
        transition="done",
        report="in_flight",
        report_timestamp="2026-08-07T12:00:00+00:00",
        watermark_message_id="100",
        reconcile_upper="200",
        reconcile_attempts=10,
    )
    monkeypatch.setattr(
        repair_report_consumer,
        "find_report",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("sensitive lookup detail")),
    )

    repair_report_consumer.consume_once()
    repair_report_consumer.consume_once()

    assert _saved_record(sandbox, request.request_id)["report"] == "paused"
    assert capsys.readouterr().err.count("paused") == 1
    assert list(sandbox.ack.iterdir()) == []


def test_reconcile_report_attempt_eleven_pauses_without_discard(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _seed(
        sandbox,
        request,
        transition="done",
        report="in_flight",
        report_timestamp="2026-08-07T12:00:00+00:00",
        watermark_message_id="100",
        reconcile_upper="200",
        report_attempts=11,
    )

    repair_report_consumer.consume_once()

    assert _saved_record(sandbox, request.request_id)["report"] == "paused"
    assert list(sandbox.ack.iterdir()) == []


def test_reconcile_transport_accept_then_raise_is_confirmed_without_resend(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    _enqueue(sandbox, request)
    sends = 0

    def accepted_then_raise(*_args, **_kwargs) -> str:
        nonlocal sends
        sends += 1
        raise OSError("accepted response lost")

    monkeypatch.setattr(repair_report_consumer, "send_report", accepted_then_raise)
    assert repair_report_consumer.consume_once() == 0
    monkeypatch.setattr(repair_report_consumer, "find_report", lambda **_kwargs: (True, "101", True))

    assert repair_report_consumer.consume_once() == 1

    assert sends == 1


def test_reconcile_shared_get_budget_caps_fifty_pages_and_defers_remainder(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records: dict[str, dict[str, str | int]] = {}
    reservations: dict[str, str] = {}
    for index in range(30):
        request = _request(f"{index + 1:032x}", occurrence=str(index + 1))
        record = _record(request, transition="done", report="in_flight")
        record.update(
            report_timestamp="2026-08-07T12:00:00+00:00",
            watermark_message_id="0",
            reconcile_upper=str(1000 + index * 10),
        )
        records[request.request_id] = record
        reservations[repair_report_queue.semantic_key(request)] = request.request_id
    _write_state(sandbox, records, reservations)
    monkeypatch.setattr(
        repair_report_send,
        "load_config",
        lambda: {"agent_id": "agent-test", "agents_log_channel_id": "channel-test"},
    )
    monkeypatch.setattr(repair_report_send, "_bot_user_id_cache", None)
    monkeypatch.setattr(repair_report_consumer, "find_report", repair_report_send.find_report)
    pages: list[str] = []

    def fetch(path: str) -> JsonValue:
        if path == "/users/@me":
            return {"id": "999"}
        pages.append(path)
        before = int(path.split("before=", 1)[1].split("&", 1)[0])
        if before % 10 == 1:
            return [{"id": str(before - 1), "author": {"id": "999"}, "content": "invalid"}]
        return []

    monkeypatch.setattr(repair_report_consumer, "_discord_fetch", fetch)

    repair_report_consumer.consume_once()

    state = _json(_state_path(sandbox))
    saved = state["records"]
    assert isinstance(saved, dict)
    untouched = list(saved.values())[25:]
    assert len(pages) == 50
    assert all(isinstance(record, dict) and record["reconcile_attempts"] == 0 for record in untouched)


def test_reconcile_shared_post_budget_caps_new_and_resend_attempts_at_twenty(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records: dict[str, dict[str, str | int]] = {}
    reservations: dict[str, str] = {}
    for index in range(15):
        request = _request(f"{index + 1:032x}", occurrence=str(index + 1))
        record = _record(request, transition="done", report="in_flight")
        record.update(
            report_timestamp="2026-08-07T12:00:00+00:00",
            watermark_message_id="100",
            reconcile_upper="200",
        )
        records[request.request_id] = record
        reservations[repair_report_queue.semantic_key(request)] = request.request_id
    _write_state(sandbox, records, reservations)
    fresh = [_request(f"{index + 101:032x}", occurrence=str(index + 101)) for index in range(15)]
    _enqueue(sandbox, *fresh)
    monkeypatch.setattr(repair_report_consumer, "find_report", lambda **_kwargs: (False, "101", True))
    attempts = 0

    def send(*_args, **kwargs) -> str:
        nonlocal attempts
        budget = kwargs["budget"]
        budget()
        attempts += 1
        return "1234"

    monkeypatch.setattr(repair_report_consumer, "send_report", send)

    repair_report_consumer.consume_once()

    assert attempts == 20


def test_transition_and_reconcile_attempt_counters_are_independent(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transition_request = _request("a" * 32)
    _enqueue(sandbox, transition_request)
    monkeypatch.setattr(repair_report_consumer, "card_state", lambda _ticket: _card("ready"))
    monkeypatch.setattr(repair_report_consumer, "_run_kanban", lambda *_args: None)
    repair_report_consumer.consume_once()
    transition_record = _saved_record(sandbox, transition_request.request_id)

    report_request = _request("b" * 32, occurrence="2")
    _seed(
        sandbox,
        report_request,
        transition="done",
        report="in_flight",
        report_timestamp="2026-08-07T12:00:00+00:00",
        watermark_message_id="100",
        reconcile_upper="200",
    )
    monkeypatch.setattr(repair_report_consumer, "find_report", lambda **_kwargs: (False, "101", True))
    repair_report_consumer.consume_once()
    report_record = _saved_record(sandbox, report_request.request_id)

    assert (transition_record["transition_attempts"], transition_record["report_attempts"]) == (1, 0)
    assert (report_record["transition_attempts"], report_record["report_attempts"]) == (0, 1)


def test_reconcile_timestamp_is_strictly_monotonic_when_clock_moves_backward(
    sandbox: Sandbox,
) -> None:
    request = _request()
    _enqueue(sandbox, request)
    future = (datetime.now(tz=UTC) + timedelta(days=1)).isoformat()
    path = _state_path(sandbox)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"records": {}, "reservations": {}, "last_timestamp": future}), encoding="utf-8")

    repair_report_consumer.consume_once()

    record = _saved_record(sandbox, request.request_id)
    assert datetime.fromisoformat(str(record["report_timestamp"])) > datetime.fromisoformat(future)


def test_reconcile_logs_neither_lookup_result_nor_message_content(
    sandbox: Sandbox,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _request()
    secret_detail = "private-message-content"
    _seed(
        sandbox,
        request,
        transition="done",
        report="in_flight",
        report_timestamp="2026-08-07T12:00:00+00:00",
        watermark_message_id="100",
        reconcile_upper="200",
    )
    monkeypatch.setattr(
        repair_report_consumer,
        "find_report",
        lambda **_kwargs: (_ for _ in ()).throw(OSError(secret_detail)),
    )

    repair_report_consumer.consume_once()

    output = capsys.readouterr().out + capsys.readouterr().err
    assert secret_detail not in output
    assert request.ticket_id not in output


def test_transition_card_state_parses_last_block_event_and_passes_direct_agent_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "task": {"status": "blocked"},
        "events": [
            {"kind": "blocked", "payload": {"kind": "transient"}},
            {"kind": "blocked", "payload": {"kind": "needs_input"}},
        ],
    }
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs["env"]))
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(repair_report_consumer.subprocess, "run", run)

    state = _REAL_CARD_STATE("t_consumer")

    assert state == _card("blocked", "needs_input")
    assert calls[0][0] == ["hermes", "kanban", "show", "t_consumer", "--json"]
    assert calls[0][1]["PATH"].startswith(f"{Path.home()}/.local/bin:")


def _watch_path() -> Path:
    return Path(__file__).parents[2] / "automation" / "repair" / "cron" / "repair_report_consume_watch.py"


def _write_watch_runtime(runtime: Path) -> None:
    package = runtime / "automation" / "repair"
    package.mkdir(parents=True)
    (runtime / "automation" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "repair_report_consumer.py").write_text(
        """import json
import os
import sys
from pathlib import Path

def consume_once() -> int:
    observation = {
        "token": os.environ.get("DISCORD_BOT_TOKEN"),
        "sys_path": sys.path,
    }
    Path(os.environ["WATCH_OBSERVATION"]).write_text(json.dumps(observation), encoding="utf-8")
    if os.environ.get("WATCH_RAISE"):
        raise RuntimeError("sensitive failure detail")
    return int(os.environ.get("WATCH_RETURN", "7"))
""",
        encoding="utf-8",
    )


def _run_watch(
    home: Path,
    runtime: Path,
    *,
    environment: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, JsonValue]]:
    import sys

    assert _watch_path().is_file(), "watch wrapper is not implemented"
    observation = home / "watch-observation.json"
    child_env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
        "REPAIR_REPORT_RUNTIME": str(runtime),
        "WATCH_OBSERVATION": str(observation),
        **(environment or {}),
    }
    completed = subprocess.run(
        [sys.executable, str(_watch_path())],
        check=False,
        capture_output=True,
        text=True,
        env=child_env,
    )
    payload: JsonValue = json.loads(observation.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return completed, payload


def test_watch_merges_secrets_into_process_environment(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    (home / ".env.secrets").write_text("DISCORD_BOT_TOKEN=from-secrets\n", encoding="utf-8")
    _write_watch_runtime(runtime)

    completed, observation = _run_watch(home, runtime)

    assert completed.returncode == 0
    assert observation["token"] == "from-secrets"


def test_watch_preserves_present_empty_environment_value(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    (home / ".env.secrets").write_text("DISCORD_BOT_TOKEN=from-secrets\n", encoding="utf-8")
    _write_watch_runtime(runtime)

    completed, observation = _run_watch(home, runtime, environment={"DISCORD_BOT_TOKEN": ""})

    assert completed.returncode == 0
    assert observation["token"] == ""


def test_watch_continues_when_secrets_file_is_absent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    _write_watch_runtime(runtime)

    completed, observation = _run_watch(home, runtime)

    assert completed.returncode == 0
    assert observation["token"] is None


def test_watch_logs_only_returned_count_on_success(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    _write_watch_runtime(runtime)

    completed, _observation = _run_watch(home, runtime, environment={"WATCH_RETURN": "13"})

    assert completed.stdout == "repair report consume watch: 13\n"
    assert completed.stderr == ""


def test_watch_returns_nonzero_with_masked_error_when_consumer_raises(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    _write_watch_runtime(runtime)

    completed, _observation = _run_watch(home, runtime, environment={"WATCH_RAISE": "1"})

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr == "repair report consume watch failed: RuntimeError\n"
    assert "sensitive failure detail" not in completed.stderr


def test_watch_sys_path_contains_no_srv_prefix(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = tmp_path / "runtime"
    _write_watch_runtime(runtime)

    _completed, observation = _run_watch(home, runtime)

    paths = observation["sys_path"]
    assert isinstance(paths, list)
    assert all(isinstance(path, str) and not path.startswith("/srv") for path in paths)


def test_watch_respects_runtime_override(tmp_path: Path) -> None:
    home = tmp_path / "home"
    override = tmp_path / "agent-owned-runtime"
    _write_watch_runtime(override)

    _completed, observation = _run_watch(home, override)

    paths = observation["sys_path"]
    assert isinstance(paths, list)
    assert paths[0] == str(override)


def test_watch_filename_is_unique_repo_wide() -> None:
    repo_root = Path(__file__).parents[2]

    matches = [
        path
        for root in (repo_root / "automation", repo_root / "skills")
        for path in root.rglob("repair_report_consume_watch.py")
    ]

    assert matches == [_watch_path()]
