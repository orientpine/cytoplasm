"""Pending/archive generation contracts for todo approvals."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "todo" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


def _spec(module):
    return module.TodoApprovalSpec(
        key="todo:sha256:fixture",
        action_hash="sha256:fixture",
        target_id="tool:gws_tasks_mutation:gws",
        argv_summary="gws tasks tasks insert --params [masked] --json [masked]",
        kind="todo",
        surface="owner-dm",
        channel_id="1526487935975952385",
        policy_version=7,
    )


def test_pending_write_is_atomic_and_carries_the_complete_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an empty checkout-external todo approval store.
    module = import_module("todo_approval_store")
    store = module.TodoApprovalStore(tmp_path)
    replaced: list[tuple[Path, Path]] = []
    real_replace = module._io.os.replace

    def replace(source: Path, target: Path) -> None:
        replaced.append((source, target))
        real_replace(source, target)

    monkeypatch.setattr(module._io.os, "replace", replace)

    # When: generation one is prepared and its posted message id is committed.
    pending = store.prepare(_spec(module), _NOW)
    bound = store.bind_message(pending, "1530000000000000001")
    payload = json.loads(store.pending_path(pending.key).read_text(encoding="utf-8"))

    # Then: tmp+replace was used and every binding/ledger field is durable.
    assert replaced
    assert payload == {
        "action_hash": "sha256:fixture",
        "argv_summary": "gws tasks tasks insert --params [masked] --json [masked]",
        "channel_id": "1526487935975952385",
        "created_at": "2026-08-16T12:00:00+00:00",
        "generation": 1,
        "key": "todo:sha256:fixture",
        "kind": "todo",
        "message_id": "1530000000000000001",
        "outcome": None,
        "policy_version": 7,
        "state": "pending",
        "surface": "owner-dm",
        "target_id": "tool:gws_tasks_mutation:gws",
    }
    assert bound.message_id == "1530000000000000001"


def test_message_id_compare_and_swap_refuses_overwrite(tmp_path: Path) -> None:
    # Given: one pending generation whose message id is already bound.
    module = import_module("todo_approval_store")
    store = module.TodoApprovalStore(tmp_path)
    pending = store.prepare(_spec(module), _NOW)
    bound = store.bind_message(pending, "1530000000000000001")

    # When / Then: a different message id cannot overwrite that immutable binding.
    with pytest.raises(module.TodoApprovalStoreError):
        store.bind_message(bound, "1530000000000000002")
    assert store.active(bound.key) == bound


def test_expiry_archives_generation_and_prepares_the_next_without_deleting_message(
    tmp_path: Path,
) -> None:
    # Given: a bound generation older than the 24-hour pending window.
    module = import_module("todo_approval_store")
    store = module.TodoApprovalStore(tmp_path)
    first = store.bind_message(store.prepare(_spec(module), _NOW), "1530000000000000001")

    # When: the same key is prepared after expiry.
    second = store.prepare(_spec(module), _NOW + module.TODO_APPROVAL_TTL + timedelta(seconds=1))

    # Then: generation one is immutable expired history and only generation two is active.
    archived = store.archives(first.key)
    assert [(item.generation, item.state, item.message_id) for item in archived] == [
        (1, module.ApprovalState.EXPIRED, "1530000000000000001")
    ]
    assert second.generation == 2
    assert second.message_id is None
    assert store.outstanding(first.key) == ()
    assert store.active(first.key) == second


def test_archived_records_are_excluded_and_never_overwritten(tmp_path: Path) -> None:
    # Given: expiry has already written generation one to its immutable archive path.
    module = import_module("todo_approval_store")
    store = module.TodoApprovalStore(tmp_path)
    first = store.bind_message(store.prepare(_spec(module), _NOW), "1530000000000000001")
    _ = store.prepare(_spec(module), _NOW + module.TODO_APPROVAL_TTL + timedelta(seconds=1))
    archive_path = store.archive_path(first.key, first.generation)
    before = archive_path.read_bytes()

    # When / Then: a conflicting stale archive attempt fails and cannot re-enter outstanding().
    with pytest.raises(module.TodoApprovalStoreError):
        store.archive(first, module.ApprovalState.ARCHIVED, None)
    assert archive_path.read_bytes() == before
    assert all(item.generation != first.generation for item in store.outstanding(first.key))


def test_archive_retry_finishes_pending_cleanup_after_interrupted_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an archive write succeeds but the process is interrupted before pending cleanup.
    module = import_module("todo_approval_store")
    store = module.TodoApprovalStore(tmp_path)
    pending = store.bind_message(store.prepare(_spec(module), _NOW), "1530000000000000001")
    pending_path = store.pending_path(pending.key)
    real_unlink = Path.unlink
    interrupted = False

    def interrupt_once(path: Path, *, missing_ok: bool = False) -> None:
        nonlocal interrupted
        if path == pending_path and not interrupted:
            interrupted = True
            raise OSError("simulated interruption after archive write")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", interrupt_once)
    with pytest.raises(OSError, match="simulated interruption"):
        store.archive(pending, module.ApprovalState.ARCHIVED, "approved")
    monkeypatch.setattr(Path, "unlink", real_unlink)

    # When: the same terminal transition is retried after restart.
    archived = store.archive(pending, module.ApprovalState.ARCHIVED, "approved")

    # Then: the immutable archive is reused and the stale pending slot is removed.
    assert archived.state is module.ApprovalState.ARCHIVED
    assert store.active(pending.key) is None
    assert store.archives(pending.key) == (archived,)


def test_pending_atomic_writes_use_distinct_temporary_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two consecutive writes target the same pending generation.
    module = import_module("todo_approval_store")
    store = module.TodoApprovalStore(tmp_path)
    temporary_paths: list[Path] = []
    real_replace = module._io.os.replace

    def capture_replace(source: Path, target: Path) -> None:
        temporary_paths.append(source)
        real_replace(source, target)

    monkeypatch.setattr(module._io.os, "replace", capture_replace)

    # When: prepare and message binding each commit pending state.
    pending = store.prepare(_spec(module), _NOW)
    store.bind_message(pending, "1530000000000000001")

    # Then: each write owns a unique temporary file in the destination directory.
    assert len(temporary_paths) == 2
    assert len(set(temporary_paths)) == 2
    assert all(path.parent == store.pending_path(pending.key).parent for path in temporary_paths)
