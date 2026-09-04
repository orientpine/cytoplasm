"""A vault write that fails must say so — on the record, on stderr, and in the status skill.

2026-09-02 실측: obsidian-write 클론의 fetch 가 120초 타임아웃으로 죽자 ``write()`` 가 예외를
삼켜 레코드가 ``approved`` 에 머물렀고, 소유자는 ✅ 뒤 아무 신호도 받지 못했다.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

from automation.obsidian_write.config import ObsidianWriteError
from automation.plaud_sync.effects_live import record_write_failure
from automation.plaud_sync.model import PlaudSyncRecord, PlaudSyncState
from automation.plaud_sync.store import PlaudSyncStore, load_state, save_state
from automation.plaud_sync.watch_step import ResolveResult

_REPO: Final = Path(__file__).resolve().parents[2]
_WATCH: Final = _REPO / "automation" / "plaud_sync" / "cron" / "plaud_sync_watch.py"

_BASE = PlaudSyncRecord(
    version=1,
    recording_id="rec-a",
    recorded_at="2026-09-01T08:00:00Z",
    note_relpath="000_PARA/Area/Lifelog/2026/2026-09-01-a--abcdef123456.md",
    note_title="a (2026-09-01)",
    body_sha256="a" * 64,
    action_hash=f"sha256:{'b' * 64}",
    status="approved",
    kind="obsidian-write",
    surface="agent-chat-thread",
    channel_id="111",
    policy_version=8,
    message_id="m-a",
    created_at="2026-09-01T09:00:00Z",
    approved_at="2026-09-02T14:30:00Z",
    written_at=None,
    remote_ref=None,
    note_content_sha256=None,
    last_block_reason=None,
    approval_thread_id="111",
)


def _state(record: PlaudSyncRecord) -> PlaudSyncState:
    return PlaudSyncState(1, None, {record.recording_id: record})


def test_record_write_failure_pins_the_reason_and_reports_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "state.json"
    save_state(path, _state(_BASE))
    error = ObsidianWriteError("Obsidian write fetch before upsert failed", True)
    record_write_failure(PlaudSyncStore(path), _BASE, error)
    after = load_state(path).records["rec-a"]
    assert after.status == "approved"
    assert after.last_block_reason == (
        "write: ObsidianWriteError: Obsidian write fetch before upsert failed"
    )
    assert "plaud-sync write error: rec-a" in capsys.readouterr().err


def _load_watch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("plaud_sync_watch_failure_test", _WATCH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_merge_effect_bindings_carries_the_write_failure_reason_into_the_saved_state() -> None:
    watch = _load_watch()
    initial = _state(_BASE)
    result = ResolveResult(initial, (), (), ())
    current = _state(replace(_BASE, last_block_reason="write: ObsidianWriteError: fetch timed out"))
    merged = watch._merge_effect_bindings(initial, result, current)
    assert merged.state.records["rec-a"].last_block_reason == (
        "write: ObsidianWriteError: fetch timed out"
    )
