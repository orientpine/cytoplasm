from __future__ import annotations

import fcntl
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from automation.memory_curator.curator import parse_memory_file
from automation.memory_curator.deletion import DeletionError, delete_entry

ORIGINAL: Final = b"first entry\n\xc2\xa7\nsecond entry\n\xc2\xa7\nthird entry"
NOW: Final = datetime(2026, 7, 30, 12, 34, 56, tzinfo=UTC)
BACKUP_NAME: Final = "MEMORY.md.deleted-20260730T123456Z"


def test_delete_entry_when_index_is_valid_preserves_order_and_exact_backup(
    tmp_path: Path,
) -> None:
    # Given: a three-entry memory file and its exact planning snapshot.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(ORIGINAL)

    # When: the middle entry is deleted by its validated index.
    outcome = delete_entry(
        tmp_path,
        "memory",
        1,
        expected_bytes=ORIGINAL,
        now=NOW,
    )

    # Then: only the middle entry is gone and the original bytes are recoverable.
    rewritten = memory_path.read_text(encoding="utf-8")
    parsed = parse_memory_file(rewritten, kind="memory")
    original = parse_memory_file(ORIGINAL.decode(), kind="memory")
    assert tuple(entry.text for entry in parsed.entries) == (
        "first entry",
        "third entry",
    )
    assert rewritten == "first entry\n§\nthird entry"
    assert len(parsed.entries) == 2
    assert outcome.backup_path == tmp_path / BACKUP_NAME
    assert outcome.backup_path.read_bytes() == ORIGINAL
    assert stat.S_IMODE(outcome.backup_path.stat().st_mode) == 0o600
    assert outcome.before_chars == original.char_count
    assert outcome.after_chars == parsed.char_count
    assert outcome.after_chars < outcome.before_chars


def test_delete_entry_when_snapshot_drifted_mutates_nothing(tmp_path: Path) -> None:
    # Given: disk bytes that differ from the caller's stale planning snapshot.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(ORIGINAL)

    # When: deletion re-verifies the stale snapshot under its lock.
    with pytest.raises(DeletionError):
        _ = delete_entry(
            tmp_path,
            "memory",
            1,
            expected_bytes=b"stale snapshot",
            now=NOW,
        )

    # Then: neither the memory nor a deletion backup was mutated.
    assert memory_path.read_bytes() == ORIGINAL
    assert list(tmp_path.glob("MEMORY.md.deleted-*")) == []


def test_delete_entry_when_memory_path_is_symlink_never_follows_it(
    tmp_path: Path,
) -> None:
    # Given: MEMORY.md is a symlink to an otherwise valid target.
    target = tmp_path / "target.md"
    _ = target.write_bytes(ORIGINAL)
    memory_path = tmp_path / "MEMORY.md"
    memory_path.symlink_to(target.name)

    # When: deletion validates the disk path.
    with pytest.raises(DeletionError):
        _ = delete_entry(
            tmp_path,
            "memory",
            1,
            expected_bytes=ORIGINAL,
            now=NOW,
        )

    # Then: the symlink and its target remain untouched.
    assert memory_path.is_symlink()
    assert target.read_bytes() == ORIGINAL
    assert list(tmp_path.glob("MEMORY.md.deleted-*")) == []


@pytest.mark.parametrize("index", [5, -1])
def test_delete_entry_when_index_is_out_of_range_mutates_nothing(
    tmp_path: Path,
    index: int,
) -> None:
    # Given: a three-entry memory file and an invalid positional index.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(ORIGINAL)

    # When: deletion validates the index after re-reading the snapshot.
    with pytest.raises(DeletionError):
        _ = delete_entry(
            tmp_path,
            "memory",
            index,
            expected_bytes=ORIGINAL,
            now=NOW,
        )

    # Then: no destructive write or backup occurs.
    assert memory_path.read_bytes() == ORIGINAL
    assert list(tmp_path.glob("MEMORY.md.deleted-*")) == []


def test_delete_entry_when_lock_is_contended_fails_without_waiting(
    tmp_path: Path,
) -> None:
    # Given: another writer holds Hermes' sibling advisory lock.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(ORIGINAL)
    lock_path = tmp_path / "MEMORY.md.lock"
    with lock_path.open("a+b") as lock_file:
        lock_path.chmod(0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

        # When: deletion attempts a non-blocking exclusive lock.
        with pytest.raises(DeletionError):
            _ = delete_entry(
                tmp_path,
                "memory",
                1,
                expected_bytes=ORIGINAL,
                now=NOW,
            )

    # Then: contention did not mutate the memory or create a backup.
    assert memory_path.read_bytes() == ORIGINAL
    assert list(tmp_path.glob("MEMORY.md.deleted-*")) == []


def test_delete_entry_when_backup_name_collides_never_overwrites_it(
    tmp_path: Path,
) -> None:
    """지켜야 하는 성질은 "덮지 않는다"이지 "멈춘다"가 아니다.

    멈추는 것으로 그 성질을 얻으면, 같은 초에 두 번째로 회수되는 항목이 매번 막힌다
    (tick 은 시각 하나를 공유한다). 충돌은 위험 신호가 아니라 "같은 초에 두 번"이라는
    뜻뿐이므로, 기존 백업은 그대로 두고 이름만 비켜간다.
    """
    # Given: the permanent backup name for this timestamp already exists.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(ORIGINAL)
    backup_path = tmp_path / BACKUP_NAME
    _ = backup_path.write_bytes(b"existing backup")

    outcome = delete_entry(tmp_path, "memory", 1, expected_bytes=ORIGINAL, now=NOW)

    # Then: the colliding backup is untouched and the new one took another name.
    assert backup_path.read_bytes() == b"existing backup"
    assert outcome.backup_path != backup_path
    assert outcome.backup_path.read_bytes() == ORIGINAL


def test_delete_entry_when_two_entries_are_reclaimed_in_one_tick_both_succeed(
    tmp_path: Path,
) -> None:
    """한 tick 은 시각 하나를 공유한다 — 그래서 같은 파일의 두 번째 삭제가 매번 실패했다.

    `ReconcileRequest.now` 는 tick 당 하나이고 백업 이름은 초 단위다. 같은 파일에서
    2건을 회수하면 두 번째 백업 이름이 **반드시** 충돌해 `delete_failed` 로 떨어진다.
    경합이 아니라 결정적 실패다 — 2026-08-03 실측: USER.md 승인 2건 중 1건만 삭제되고
    나머지는 `delete_failed` 를 달고 다음 tick 으로 밀렸다.

    덮어쓰기 금지는 그대로다. 이름이 이미 있으면 덮지 않고 비켜간다.
    """
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(ORIGINAL)

    first = delete_entry(tmp_path, "memory", 1, expected_bytes=ORIGINAL, now=NOW)
    remaining = memory_path.read_bytes()
    second = delete_entry(tmp_path, "memory", 0, expected_bytes=remaining, now=NOW)

    assert first.backup_path != second.backup_path, "같은 이름을 두 번 썼다"
    assert first.backup_path.read_bytes() == ORIGINAL, "첫 백업이 덮어써졌다"
    assert second.backup_path.read_bytes() == remaining
    assert parse_memory_file(memory_path.read_text(encoding="utf-8"), kind="memory").entries
