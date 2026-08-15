from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from automation.interop.report import mask_summary
from automation.repair import repair_report_queue


def _request(request_id: str = "0" * 32) -> repair_report_queue.ReportRequest:
    return repair_report_queue.ReportRequest(
        request_id=request_id,
        operation="complete",
        ticket_id="t_queue1",
        reason_code="applied",
        occurrence="1",
        mac="a" * 64,
        created="2026-08-07T12:00:00+00:00",
    )


def _raw(request: repair_report_queue.ReportRequest) -> bytes:
    return json.dumps(asdict(request), sort_keys=True, separators=(",", ":")).encode()


def _enqueue_worker(queue_root: str, request_id: str) -> None:
    os.environ["REPAIR_REPORT_QUEUE"] = queue_root
    repair_report_queue.enqueue(_request(request_id))


def _deduplicate_worker(queue_root: str, request_id: str) -> bool:
    os.environ["REPAIR_REPORT_QUEUE"] = queue_root
    return repair_report_queue.enqueue_if_missing_semantic(_request(request_id))


@pytest.fixture(autouse=True)
def _isolated_queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / "queue.lock").touch(mode=0o640)
    monkeypatch.setenv("REPAIR_REPORT_QUEUE", str(queue))
    return queue


def test_01_json_round_trip_preserves_the_request() -> None:
    request = _request()

    parsed = repair_report_queue.parse_line(_raw(request))

    assert parsed == request


def test_02_enqueue_twice_appends_two_complete_json_lines(_isolated_queue: Path) -> None:
    request = _request()

    repair_report_queue.enqueue(request)
    repair_report_queue.enqueue(request)

    lines = (_isolated_queue / "pending.jsonl").read_bytes().splitlines()
    assert [repair_report_queue.parse_line(line) for line in lines] == [request, request]


def test_03_new_pending_file_has_mode_0640(_isolated_queue: Path) -> None:
    repair_report_queue.enqueue(_request())

    mode = stat.S_IMODE((_isolated_queue / "pending.jsonl").stat().st_mode)

    assert mode == 0o640


@pytest.mark.parametrize("missing_directory", [False, True])
def test_04_missing_lock_or_directory_is_no_op_without_creating_lock(
    _isolated_queue: Path,
    capsys: pytest.CaptureFixture[str],
    missing_directory: bool,
) -> None:
    (_isolated_queue / "queue.lock").unlink()
    if missing_directory:
        _isolated_queue.rmdir()

    repair_report_queue.enqueue(_request())

    assert not (_isolated_queue / "queue.lock").exists()
    assert not (_isolated_queue / "pending.jsonl").exists()
    assert "skipped" in capsys.readouterr().err


def test_05_read_only_pending_path_is_a_masked_no_op(
    _isolated_queue: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pending = _isolated_queue / "pending.jsonl"
    pending.symlink_to("/proc/version")

    repair_report_queue.enqueue(_request())

    assert pending.is_symlink()
    assert "skipped" in capsys.readouterr().err


def test_06_queue_directory_override_is_respected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    override = tmp_path / "override"
    monkeypatch.setenv("REPAIR_REPORT_QUEUE", str(override))

    assert repair_report_queue.queue_dir() == override
    assert repair_report_queue.lock_path() == override / "queue.lock"


def test_07_parallel_enqueue_produces_two_unbroken_lines(_isolated_queue: Path) -> None:
    request_ids = ["1" * 32, "2" * 32]

    with ProcessPoolExecutor(max_workers=2) as executor:
        list(executor.map(_enqueue_worker, [str(_isolated_queue)] * 2, request_ids))

    lines = (_isolated_queue / "pending.jsonl").read_bytes().splitlines()
    assert {request.request_id for line in lines if (request := repair_report_queue.parse_line(line))} == set(
        request_ids
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload | {"extra": "x"},
        lambda payload: {key: value for key, value in payload.items() if key != "mac"},
        lambda payload: payload | {"occurrence": 1},
        lambda payload: payload | {"request_id": "g" * 32},
        lambda payload: payload | {"ticket_id": "ticket1"},
        lambda payload: payload | {"operation": "--complete"},
        lambda payload: payload | {"operation": "complete", "reason_code": "bank_red"},
        lambda payload: payload | {"operation": "reopen", "reason_code": "applied"},
        lambda payload: payload | {"reason_code": "unknown"},
        lambda payload: payload | {"occurrence": "one"},
        lambda payload: payload | {"mac": "a" * 63},
        lambda payload: payload | {"created": "2026-08-07T12:00:00"},
    ],
)
def test_08_parser_rejects_every_invalid_schema_class(mutation) -> None:
    payload = mutation(asdict(_request()))

    assert repair_report_queue.parse_line(json.dumps(payload).encode()) is None


@pytest.mark.parametrize("raw", [b"{" + (b" " * 512), b"\xff\xfe"])
def test_08_parser_rejects_oversized_or_invalid_utf8_bytes(raw: bytes) -> None:
    assert repair_report_queue.parse_line(raw) is None


@pytest.mark.parametrize("delta", [-timedelta(days=200), timedelta(days=1)])
def test_09_parser_accepts_old_and_future_aware_datetimes(delta: timedelta) -> None:
    created = (datetime.now(tz=UTC) + delta).isoformat()

    parsed = repair_report_queue.parse_line(_raw(replace(_request(), created=created)))

    assert parsed is not None
    assert parsed.created == created


def test_10_line_digest_hashes_raw_bytes_even_when_utf8_is_invalid() -> None:
    raw = b"\xff\xfe\x00"

    first = repair_report_queue.line_digest(raw)

    assert first == hashlib.sha256(raw).hexdigest()
    assert repair_report_queue.line_digest(raw) == first


def test_11_semantic_key_is_stable_and_ignores_request_identity() -> None:
    first = _request("1" * 32)
    second = replace(first, request_id="2" * 32, mac="b" * 64, created="2020-01-01T00:00:00+00:00")

    assert repair_report_queue.semantic_key(first) == repair_report_queue.semantic_key(second)
    assert repair_report_queue.semantic_key(first) == hashlib.sha256(b"t_queue1|1|complete|applied").hexdigest()


def test_12_valid_record_cannot_carry_protocol_forbidden_detail() -> None:
    raw = _raw(_request()).decode()
    forbidden_ticket = "t_" + "calendar" + "_cli" + ".py"

    assert mask_summary(raw) == raw
    assert repair_report_queue.parse_line(_raw(replace(_request(), ticket_id=forbidden_ticket))) is None


def test_13_existing_semantic_request_is_not_appended(_isolated_queue: Path) -> None:
    repair_report_queue.enqueue(_request("1" * 32))
    before = (_isolated_queue / "pending.jsonl").read_bytes()

    appended = repair_report_queue.enqueue_if_missing_semantic(_request("2" * 32))

    assert not appended
    assert (_isolated_queue / "pending.jsonl").read_bytes() == before


def test_14_missing_semantic_request_is_appended_once(_isolated_queue: Path) -> None:
    appended = repair_report_queue.enqueue_if_missing_semantic(_request())

    assert appended
    assert len((_isolated_queue / "pending.jsonl").read_bytes().splitlines()) == 1


def test_15_semantic_enqueue_returns_without_lock_reentry_deadlock() -> None:
    completed: list[bool] = []

    def exercise() -> None:
        completed.append(repair_report_queue.enqueue_if_missing_semantic(_request()))

    worker = threading.Thread(target=exercise, daemon=True)
    worker.start()
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert completed == [True]


def test_16_parallel_semantic_enqueue_appends_exactly_once(_isolated_queue: Path) -> None:
    request_ids = [f"{index:032x}" for index in range(1, 6)]

    with ProcessPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(_deduplicate_worker, [str(_isolated_queue)] * 5, request_ids))

    assert results.count(True) == 1
    assert len((_isolated_queue / "pending.jsonl").read_bytes().splitlines()) == 1


def _compact_ack_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    ack = tmp_path / "ack"
    ack.mkdir()
    monkeypatch.setenv("REPAIR_REPORT_ACK", str(ack))
    return ack


def _write_receipt(path: Path, **fields: str) -> None:
    path.write_text(json.dumps({"terminal_at": "2026-08-07T13:00:00+00:00", **fields}), encoding="utf-8")


def test_17_compact_removes_only_identity_matched_ack(
    _isolated_queue: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ack = _compact_ack_dir(monkeypatch, tmp_path)
    removed_request = _request("1" * 32)
    kept_request = replace(_request("2" * 32), occurrence="2")
    removed_raw = _raw(removed_request) + b"\n"
    repair_report_queue.enqueue(removed_request)
    repair_report_queue.enqueue(kept_request)
    _write_receipt(
        ack / f"{removed_request.request_id}.json",
        line_digest=repair_report_queue.line_digest(removed_raw),
        semantic_key=repair_report_queue.semantic_key(removed_request),
    )

    removed = repair_report_queue.compact()

    assert removed == 1
    assert (_isolated_queue / "pending.jsonl").read_bytes() == _raw(kept_request) + b"\n"


def test_18_compact_preserves_reused_request_id_when_ack_digest_differs(
    _isolated_queue: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ack = _compact_ack_dir(monkeypatch, tmp_path)
    request = _request("3" * 32)
    repair_report_queue.enqueue(request)
    before = (_isolated_queue / "pending.jsonl").read_bytes()
    _write_receipt(
        ack / f"{request.request_id}.json",
        line_digest="f" * 64,
        semantic_key=repair_report_queue.semantic_key(request),
    )

    removed = repair_report_queue.compact()

    assert removed == 0
    assert (_isolated_queue / "pending.jsonl").read_bytes() == before


def test_19_compact_conflict_receipt_removes_line_and_preserves_original_ack(
    _isolated_queue: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ack = _compact_ack_dir(monkeypatch, tmp_path)
    request = _request("4" * 32)
    raw = _raw(request) + b"\n"
    repair_report_queue.enqueue(request)
    original_ack = ack / f"{request.request_id}.json"
    _write_receipt(original_ack, line_digest="e" * 64, semantic_key="d" * 64)
    original_bytes = original_ack.read_bytes()
    _write_receipt(ack / f"conflict-{repair_report_queue.line_digest(raw)}.json")

    removed = repair_report_queue.compact()

    assert removed == 1
    assert (_isolated_queue / "pending.jsonl").read_bytes() == b""
    assert original_ack.read_bytes() == original_bytes


def test_20_compact_preserves_stale_unreceipted_lines_and_warns_once(
    _isolated_queue: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _compact_ack_dir(monkeypatch, tmp_path)
    stale_created = (datetime.now(tz=UTC) - timedelta(days=200)).isoformat()
    requests = [replace(_request(f"{index:032x}"), occurrence=str(index), created=stale_created) for index in (1, 2)]
    for request in requests:
        repair_report_queue.enqueue(request)
    before = (_isolated_queue / "pending.jsonl").read_bytes()

    removed = repair_report_queue.compact()

    assert removed == 0
    assert (_isolated_queue / "pending.jsonl").read_bytes() == before
    assert capsys.readouterr().err.count("stale") == 1


def test_21_compact_removes_only_invalid_line_with_terminal_invalid_receipt(
    _isolated_queue: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ack = _compact_ack_dir(monkeypatch, tmp_path)
    receipted = b"invalid-one\n"
    unreceipted = b"invalid-two\n"
    (_isolated_queue / "pending.jsonl").write_bytes(receipted + unreceipted)
    _write_receipt(ack / f"invalid-{repair_report_queue.line_digest(receipted)}.json")

    removed = repair_report_queue.compact()

    assert removed == 1
    assert (_isolated_queue / "pending.jsonl").read_bytes() == unreceipted


@pytest.mark.parametrize(
    "receipt_bytes",
    [b"{", b'{}', b"\xff", b"[]"],
    ids=["malformed-json", "missing-terminal", "invalid-utf8", "non-object"],
)
def test_22_compact_preserves_line_for_nonterminal_or_corrupt_receipt(
    _isolated_queue: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    receipt_bytes: bytes,
) -> None:
    ack = _compact_ack_dir(monkeypatch, tmp_path)
    request = _request("5" * 32)
    repair_report_queue.enqueue(request)
    before = (_isolated_queue / "pending.jsonl").read_bytes()
    (ack / f"{request.request_id}.json").write_bytes(receipt_bytes)

    removed = repair_report_queue.compact()

    assert removed == 0
    assert (_isolated_queue / "pending.jsonl").read_bytes() == before


def test_23_compact_warns_once_when_unprocessed_backlog_exceeds_5000(
    _isolated_queue: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _compact_ack_dir(monkeypatch, tmp_path)
    (_isolated_queue / "pending.jsonl").write_bytes(b"invalid\n" * 5001)

    removed = repair_report_queue.compact()

    assert removed == 0
    assert len((_isolated_queue / "pending.jsonl").read_bytes().splitlines()) == 5001
    assert capsys.readouterr().err.count("5000") == 1


def test_24_compact_serializes_with_concurrent_enqueue_without_losing_lines(
    _isolated_queue: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ack = _compact_ack_dir(monkeypatch, tmp_path)
    removed_request = _request("6" * 32)
    kept_request = replace(_request("7" * 32), occurrence="2")
    added_request = replace(_request("8" * 32), occurrence="3")
    repair_report_queue.enqueue(removed_request)
    repair_report_queue.enqueue(kept_request)
    removed_raw = _raw(removed_request) + b"\n"
    _write_receipt(
        ack / f"{removed_request.request_id}.json",
        line_digest=repair_report_queue.line_digest(removed_raw),
        semantic_key=repair_report_queue.semantic_key(removed_request),
    )
    replacement_started = threading.Event()
    allow_replacement = threading.Event()
    enqueue_started = threading.Event()
    real_replace = repair_report_queue.os.replace

    def gated_replace(source: str | Path, destination: str | Path) -> None:
        replacement_started.set()
        assert allow_replacement.wait(timeout=3)
        real_replace(source, destination)

    monkeypatch.setattr(repair_report_queue.os, "replace", gated_replace)
    compact_worker = threading.Thread(target=repair_report_queue.compact)
    compact_worker.start()
    assert replacement_started.wait(timeout=3)

    def enqueue_concurrently() -> None:
        enqueue_started.set()
        repair_report_queue.enqueue(added_request)

    enqueue_worker = threading.Thread(target=enqueue_concurrently)
    enqueue_worker.start()
    assert enqueue_started.wait(timeout=3)
    allow_replacement.set()
    compact_worker.join(timeout=3)
    enqueue_worker.join(timeout=3)

    lines = (_isolated_queue / "pending.jsonl").read_bytes().splitlines()
    assert not compact_worker.is_alive() and not enqueue_worker.is_alive()
    assert {repair_report_queue.parse_line(line) for line in lines} == {kept_request, added_request}


def test_25_compact_is_no_op_when_ack_directory_is_absent(
    _isolated_queue: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing_ack = tmp_path / "missing-ack"
    monkeypatch.setenv("REPAIR_REPORT_ACK", str(missing_ack))
    repair_report_queue.enqueue(_request())
    before = (_isolated_queue / "pending.jsonl").read_bytes()

    removed = repair_report_queue.compact()

    assert removed == 0
    assert not missing_ack.exists()
    assert (_isolated_queue / "pending.jsonl").read_bytes() == before


def test_26_compact_replacement_has_mode_0640(
    _isolated_queue: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _compact_ack_dir(monkeypatch, tmp_path)
    repair_report_queue.enqueue(_request())

    repair_report_queue.compact()

    assert stat.S_IMODE((_isolated_queue / "pending.jsonl").stat().st_mode) == 0o640


def test_27_compact_preserves_queue_lock_inode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _compact_ack_dir(monkeypatch, tmp_path)
    before = os.stat(repair_report_queue.lock_path()).st_ino
    repair_report_queue.enqueue(_request())

    repair_report_queue.compact()

    assert os.stat(repair_report_queue.lock_path()).st_ino == before


def test_28_compact_cli_entrypoint_runs_compaction(
    _isolated_queue: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ack = _compact_ack_dir(monkeypatch, tmp_path)
    request = _request("9" * 32)
    repair_report_queue.enqueue(request)
    raw = _raw(request) + b"\n"
    _write_receipt(
        ack / f"{request.request_id}.json",
        line_digest=repair_report_queue.line_digest(raw),
        semantic_key=repair_report_queue.semantic_key(request),
    )

    result = repair_report_queue._main(["--compact"])

    assert result == 0
    assert capsys.readouterr().out.strip() == "1"
    assert (_isolated_queue / "pending.jsonl").read_bytes() == b""


def test_29_compact_ignores_reported_marker_without_receipt(
    _isolated_queue: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ack = _compact_ack_dir(monkeypatch, tmp_path)
    request = _request("a" * 32)
    repair_report_queue.enqueue(request)
    before = (_isolated_queue / "pending.jsonl").read_bytes()
    (ack / f"reported-{'b' * 64}.json").write_text(
        json.dumps(
            {
                "ticket_id": request.ticket_id,
                "operation": request.operation,
                "reason_code": request.reason_code,
                "first_reported_at": "2026-08-07T13:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    removed = repair_report_queue.compact()

    assert removed == 0
    assert (_isolated_queue / "pending.jsonl").read_bytes() == before
