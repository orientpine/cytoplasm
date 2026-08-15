from __future__ import annotations

import ast
import json
import os
import stat
import sys
import threading
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

from automation.repair import repair_capability
from automation.repair import repair_cli
from automation.repair import repair_report_consumer
from automation.repair.repair_core import RepairEvent, RepairResult


def _secret_worker(home: str) -> bytes:
    os.environ["HOME"] = home
    return repair_capability.secret()


def _publish_worker(home: str, capability_root: str, occurrence: int) -> None:
    os.environ["HOME"] = home
    os.environ["REPAIR_CAPABILITY_DIR"] = capability_root
    repair_capability.publish("t_parallel", occurrence)


def _reconcile_worker(home: str, capability_root: str, registry_path: str) -> int:
    os.environ["HOME"] = home
    os.environ["REPAIR_CAPABILITY_DIR"] = capability_root
    os.environ["REPAIR_STATE_FILE"] = registry_path
    return repair_capability.reconcile_capabilities()


def _publish_named_worker(home: str, capability_root: str, ticket_id: str) -> None:
    os.environ["HOME"] = home
    os.environ["REPAIR_CAPABILITY_DIR"] = capability_root
    repair_capability.publish(ticket_id, 1)


@dataclass(frozen=True, slots=True)
class _StubService:
    result: RepairResult

    def record(self, event: RepairEvent) -> RepairResult:
        del event
        return self.result


class _SyntheticPublishError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _isolated_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("REPAIR_CAPABILITY_DIR", str(tmp_path / "capabilities"))
    monkeypatch.setenv("REPAIR_STATE_FILE", str(tmp_path / "repair-tickets.json"))


def test_01_secret_creates_exact_key_and_private_home_directory(tmp_path: Path) -> None:
    key = repair_capability.secret()

    key_path = tmp_path / "home/.hermes/repair-report-capability.key"
    assert key_path.read_bytes() == key
    assert len(key) == 32
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_path.parent.stat().st_mode) == 0o700


def test_02_parallel_secret_callers_share_one_complete_key(tmp_path: Path) -> None:
    home = str(tmp_path / "home")

    with ProcessPoolExecutor(max_workers=20) as executor:
        keys = list(executor.map(_secret_worker, [home] * 20))

    assert len(set(keys)) == 1
    assert len(keys[0]) == 32


@pytest.mark.parametrize(("size", "mode"), [(0, 0o600), (31, 0o600), (32, 0o644)])
def test_03_invalid_existing_key_fails_closed_without_overwrite(tmp_path: Path, size: int, mode: int) -> None:
    key_path = tmp_path / "home/.hermes/repair-report-capability.key"
    key_path.parent.mkdir(parents=True)
    original = b"k" * size
    key_path.write_bytes(original)
    key_path.chmod(mode)

    with pytest.raises(repair_capability.CapabilityKeyError):
        repair_capability.secret()

    assert key_path.read_bytes() == original
    assert stat.S_IMODE(key_path.stat().st_mode) == mode


def test_04_crash_leftover_cannot_expose_a_partial_key(tmp_path: Path) -> None:
    key_dir = tmp_path / "home/.hermes"
    key_dir.mkdir(parents=True)
    child = os.fork()
    if child == 0:
        (key_dir / ".repair-report-capability.key.crashed").write_bytes(b"partial")
        os._exit(0)
    os.waitpid(child, 0)

    repair_capability.secret()

    key_path = key_dir / "repair-report-capability.key"
    assert not key_path.exists() or len(key_path.read_bytes()) == 32


def test_05_mac_is_stable_and_binds_occurrence() -> None:
    first = repair_capability.mac("t_mac", 1)

    assert repair_capability.mac("t_mac", 1) == first
    assert repair_capability.mac("t_mac", 2) != first


def test_06_verify_rejects_a_one_bit_mac_mutation() -> None:
    expected = repair_capability.mac("t_verify", 4)
    mutated = ("0" if expected[0] != "0" else "1") + expected[1:]

    assert repair_capability.verify("t_verify", 4, expected)
    assert not repair_capability.verify("t_verify", 4, mutated)


def test_07_publish_atomically_writes_a_mode_640_record(tmp_path: Path) -> None:
    capability_root = tmp_path / "capabilities"
    capability_root.mkdir()

    repair_capability.publish("t_publish", 1)

    path = capability_root / "t_publish.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"ticket_id", "occurrence", "mac", "issued_at"}
    assert all(isinstance(value, str) for value in payload.values())
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_08_publish_never_regresses_an_existing_occurrence(tmp_path: Path) -> None:
    capability_root = tmp_path / "capabilities"
    capability_root.mkdir()
    repair_capability.publish("t_monotonic", 3)
    path = capability_root / "t_monotonic.json"
    original = path.read_bytes()

    repair_capability.publish("t_monotonic", 2)

    assert path.read_bytes() == original


def test_09_parallel_publish_keeps_the_highest_valid_occurrence(tmp_path: Path) -> None:
    home = str(tmp_path / "home")
    capability_root = tmp_path / "capabilities"
    capability_root.mkdir()

    with ProcessPoolExecutor(max_workers=2) as executor:
        list(executor.map(_publish_worker, [home, home], [str(capability_root)] * 2, [1, 2]))

    payload = json.loads((capability_root / "t_parallel.json").read_text(encoding="utf-8"))
    assert payload["occurrence"] == "2"


def test_10_missing_capability_directory_is_a_masked_no_op(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repair_capability.publish("t_missing", 1)

    captured = capsys.readouterr()
    assert not (tmp_path / "capabilities/t_missing.json").exists()
    assert "skipped" in captured.err


def test_11_detect_stdout_json_contract_is_unchanged(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    result = RepairResult("t_detect", 3, True, "", "", "", "")
    monkeypatch.setattr(repair_cli, "_service", lambda: _StubService(result))
    monkeypatch.setattr(repair_cli, "publish", lambda ticket_id, occurrence: None)

    assert repair_cli._detect("source", "location", "raw") == 0

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"ticket", "occurrence", "created"}


def test_12_publish_failure_does_not_change_detect_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    result = RepairResult("t_detect", 3, False, "", "", "", "")
    monkeypatch.setattr(repair_cli, "_service", lambda: _StubService(result))

    def fail_publish(ticket_id: str, occurrence: int) -> None:
        del ticket_id, occurrence
        raise _SyntheticPublishError("synthetic publish failure")

    monkeypatch.setattr(repair_cli, "publish", fail_publish)

    assert repair_cli._detect("source", "location", "raw") == 0
    assert set(json.loads(capsys.readouterr().out)) == {"ticket", "occurrence", "created"}


def test_13_key_bytes_never_appear_in_output_or_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    key_path = tmp_path / "home/.hermes/repair-report-capability.key"
    key_path.parent.mkdir(parents=True)
    key_path.write_bytes(b"s" * 32)
    key_path.chmod(0o644)

    with pytest.raises(repair_capability.CapabilityKeyError) as raised:
        repair_capability.secret()

    captured = capsys.readouterr()
    combined = captured.out + captured.err + str(raised.value)
    assert "s" * 32 not in combined
    assert (b"s" * 32).hex() not in combined


def test_14_module_imports_only_stdlib_or_same_package() -> None:
    source = Path(repair_capability.__file__).read_text(encoding="utf-8")
    imports = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import | ast.ImportFrom)]

    for imported in imports:
        root = imported.names[0].name.split(".")[0] if isinstance(imported, ast.Import) else (imported.module or "").split(".")[0]
        assert root in sys.stdlib_module_names | {"__future__", "automation"}


def test_15_all_paths_resolve_inside_test_temporary_directory(tmp_path: Path) -> None:
    assert repair_capability._lock_path().is_relative_to(tmp_path)
    assert repair_capability.capability_dir().is_relative_to(tmp_path)


def test_16_integer_publish_serializes_occurrence_as_canonical_string(tmp_path: Path) -> None:
    capability_root = tmp_path / "capabilities"
    capability_root.mkdir()

    repair_capability.publish("t_string", 3)

    payload = json.loads((capability_root / "t_string.json").read_text(encoding="utf-8"))
    assert payload["occurrence"] == "3"
    assert isinstance(payload["occurrence"], str)


@pytest.mark.parametrize("occurrence", [3, "007", "1234567890"])
def test_17_read_published_rejects_noncanonical_occurrence(tmp_path: Path, occurrence: int | str) -> None:
    capability_root = tmp_path / "capabilities"
    capability_root.mkdir()
    path = capability_root / "t_invalid.json"
    path.write_text(json.dumps({"ticket_id": "t_invalid", "occurrence": occurrence, "mac": "x", "issued_at": "x"}))

    assert repair_capability.read_published("t_invalid") is None


def test_18_publish_and_mac_return_without_lock_reentry_deadlock(tmp_path: Path) -> None:
    (tmp_path / "capabilities").mkdir()
    completed: list[bool] = []

    def exercise() -> None:
        repair_capability.publish("t_deadlock", 1)
        repair_capability.mac("t_deadlock", 1)
        completed.append(True)

    worker = threading.Thread(target=exercise, daemon=True)
    worker.start()
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert completed == [True]


def test_19_verify_normalizes_integer_and_string_occurrences() -> None:
    expected = repair_capability.mac("t_mixed", "3")

    assert repair_capability.verify("t_mixed", 3, expected)


def test_reconcile_publishes_every_missing_registry_capability(tmp_path: Path) -> None:
    # Given
    (tmp_path / "capabilities").mkdir()
    registry = {
        "sig-a": {"ticket_id": "t_reconcile_a", "occurrences": 1},
        "sig-b": {"ticket_id": "t_reconcile_b", "occurrences": 3},
        "sig-c": {"ticket_id": "t_reconcile_c", "occurrences": 7},
    }
    (tmp_path / "repair-tickets.json").write_text(json.dumps(registry), encoding="utf-8")

    # When
    published = repair_capability.reconcile_capabilities()

    # Then
    assert published == 3
    for ticket_id, occurrences in (
        ("t_reconcile_a", 1),
        ("t_reconcile_b", 3),
        ("t_reconcile_c", 7),
    ):
        record = repair_capability.read_published(ticket_id)
        assert record is not None
        assert record["occurrence"] == str(occurrences)


def test_reconcile_leaves_current_capability_mtime_unchanged(tmp_path: Path) -> None:
    # Given
    capability_root = tmp_path / "capabilities"
    capability_root.mkdir()
    repair_capability.publish("t_reconcile_current", 4)
    capability_path = capability_root / "t_reconcile_current.json"
    original_mtime = capability_path.stat().st_mtime_ns
    registry = {"sig": {"ticket_id": "t_reconcile_current", "occurrences": 4}}
    (tmp_path / "repair-tickets.json").write_text(json.dumps(registry), encoding="utf-8")

    # When
    published = repair_capability.reconcile_capabilities()

    # Then
    assert published == 0
    assert capability_path.stat().st_mtime_ns == original_mtime


def test_reconcile_updates_stale_but_never_regresses_newer_capability(tmp_path: Path) -> None:
    # Given
    capability_root = tmp_path / "capabilities"
    capability_root.mkdir()
    repair_capability.publish("t_reconcile_stale", 1)
    repair_capability.publish("t_reconcile_newer", 9)
    newer_path = capability_root / "t_reconcile_newer.json"
    newer_bytes = newer_path.read_bytes()
    registry = {
        "sig-stale": {"ticket_id": "t_reconcile_stale", "occurrences": 5},
        "sig-newer": {"ticket_id": "t_reconcile_newer", "occurrences": 6},
    }
    (tmp_path / "repair-tickets.json").write_text(json.dumps(registry), encoding="utf-8")

    # When
    published = repair_capability.reconcile_capabilities()

    # Then
    stale = repair_capability.read_published("t_reconcile_stale")
    assert published == 1
    assert stale is not None
    assert stale["occurrence"] == "5"
    assert newer_path.read_bytes() == newer_bytes


@pytest.mark.parametrize("registry_payload", [None, "{damaged", "[]"])
def test_reconcile_registry_failure_returns_zero_with_one_masked_warning(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    registry_payload: str | None,
) -> None:
    # Given
    (tmp_path / "capabilities").mkdir()
    if registry_payload is not None:
        (tmp_path / "repair-tickets.json").write_text(registry_payload, encoding="utf-8")

    # When
    published = repair_capability.reconcile_capabilities()

    # Then
    warning_lines = capsys.readouterr().err.splitlines()
    assert published == 0
    assert len(warning_lines) == 1
    assert str(tmp_path) not in warning_lines[0]


def test_reconcile_missing_capability_directory_returns_zero(tmp_path: Path) -> None:
    # Given
    registry = {"sig": {"ticket_id": "t_reconcile_no_dir", "occurrences": 2}}
    (tmp_path / "repair-tickets.json").write_text(json.dumps(registry), encoding="utf-8")

    # When
    published = repair_capability.reconcile_capabilities()

    # Then
    assert published == 0


def test_reconcile_limit_carries_remaining_entries_to_next_call(tmp_path: Path) -> None:
    # Given
    (tmp_path / "capabilities").mkdir()
    registry = {
        f"sig-{index}": {"ticket_id": f"t_reconcile_limit_{index}", "occurrences": index + 1}
        for index in range(3)
    }
    (tmp_path / "repair-tickets.json").write_text(json.dumps(registry), encoding="utf-8")

    # When
    first = repair_capability.reconcile_capabilities(limit=2)
    second = repair_capability.reconcile_capabilities(limit=2)

    # Then
    assert (first, second) == (2, 1)
    assert len(list((tmp_path / "capabilities").glob("*.json"))) == 3


def test_reconcile_never_changes_registry_bytes(tmp_path: Path) -> None:
    # Given
    (tmp_path / "capabilities").mkdir()
    registry_path = tmp_path / "repair-tickets.json"
    registry_path.write_bytes(b'{"sig":{"ticket_id":"t_reconcile_readonly","occurrences":2}}\n')
    before = registry_path.read_bytes()

    # When
    repair_capability.reconcile_capabilities()

    # Then
    assert registry_path.read_bytes() == before


def test_reconcile_consumer_calls_once_before_queue_and_continues_after_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    events: list[str] = []
    empty_state = repair_report_consumer.ConsumerState(records={}, reservations={}, last_timestamp="")

    def fail_reconcile() -> int:
        events.append("reconcile")
        raise RuntimeError("synthetic reconcile failure")

    def snapshot() -> list[bytes]:
        events.append("snapshot")
        return []

    monkeypatch.setattr(repair_report_consumer, "reconcile_capabilities", fail_reconcile)
    monkeypatch.setattr(repair_report_consumer, "_load_state", lambda: empty_state)
    monkeypatch.setattr(repair_report_consumer, "_load_ticket_allowlist", frozenset)
    monkeypatch.setattr(
        repair_report_consumer,
        "_reconcile_start_records",
        lambda state, budgets: (state, 0, frozenset()),
    )
    monkeypatch.setattr(repair_report_consumer, "_snapshot_lines", snapshot)

    # When
    completed = repair_report_consumer.consume_once()

    # Then
    assert completed == 0
    assert events == ["reconcile", "snapshot"]


def test_reconcile_concurrent_publish_keeps_both_records_valid(tmp_path: Path) -> None:
    # Given
    home = str(tmp_path / "home")
    capability_root = tmp_path / "capabilities"
    capability_root.mkdir()
    registry_path = tmp_path / "repair-tickets.json"
    registry_path.write_text(
        json.dumps({"sig": {"ticket_id": "t_reconcile_parallel", "occurrences": 3}}),
        encoding="utf-8",
    )

    # When
    with ProcessPoolExecutor(max_workers=2) as executor:
        reconcile_future = executor.submit(
            _reconcile_worker,
            home,
            str(capability_root),
            str(registry_path),
        )
        publish_future = executor.submit(
            _publish_named_worker,
            home,
            str(capability_root),
            "t_reconcile_other",
        )
        reconciled = reconcile_future.result()
        publish_future.result()

    # Then
    reconciled_record = repair_capability.read_published("t_reconcile_parallel")
    other_record = repair_capability.read_published("t_reconcile_other")
    assert reconciled == 1
    assert reconciled_record is not None
    assert other_record is not None
    assert reconciled_record["occurrence"] == "3"
    assert other_record["occurrence"] == "1"


def test_reconcile_paths_are_all_inside_test_temporary_directory(tmp_path: Path) -> None:
    # Given / When
    registry_path = repair_capability.registry_path()
    capability_root = repair_capability.capability_dir()
    lock = repair_capability._lock_path()

    # Then
    assert registry_path.is_relative_to(tmp_path)
    assert capability_root.is_relative_to(tmp_path)
    assert lock.is_relative_to(tmp_path)
