"""Re-posting live approval cards after a renderer change (owner request 2026-09-02).

The lifecycle façade re-posts only on a content change; a card format change keeps
the action hash, so the old card must be deleted and the record returned to
``planned`` for the next tick to render it again — in the thread it already has.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Final
from urllib.error import HTTPError

import pytest

from automation.plaud_sync.model import PlaudSyncRecord, PlaudSyncState
from automation.plaud_sync.repost import (
    reprocess,
    repost_posted,
    reset_for_reprocess,
    reset_for_repost,
)
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
    status="posted",
    kind="obsidian-write",
    surface="agent-chat-thread",
    channel_id="111",
    policy_version=8,
    message_id="m-a",
    created_at="2026-09-01T09:00:00Z",
    approved_at=None,
    written_at=None,
    remote_ref=None,
    note_content_sha256=None,
    last_block_reason=None,
    approval_thread_id="111",
)


def _record(**overrides: object) -> PlaudSyncRecord:
    return replace(_BASE, **overrides)


class _Transport:
    owner_id = "owner-1"

    def __init__(self, failing: dict[str, int] | None = None) -> None:
        self.deleted: list[tuple[str, str]] = []
        self.failing = failing or {}

    def delete_message(self, channel_id: str, message_id: str) -> None:
        code = self.failing.get(message_id)
        if code is not None:
            raise HTTPError("https://discord.invalid/x", code, "err", Message(), None)
        self.deleted.append((channel_id, message_id))

    def post_message(self, channel_id: str, content: str) -> str:
        raise AssertionError("repost never posts by itself")

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        raise AssertionError("repost never reacts")

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        raise AssertionError("repost never reads")

    def get_reaction_users(
        self, channel_id: str, message_id: str, emoji: str
    ) -> tuple[tuple[str, bool], ...]:
        raise AssertionError("repost never probes")


def _state_file(tmp_path: Path, *records: PlaudSyncRecord) -> Path:
    path = tmp_path / "state.json"
    save_state(path, PlaudSyncState(1, None, {r.recording_id: r for r in records}))
    return path


def test_reset_for_repost_returns_a_posted_record_to_planned_in_its_own_thread() -> None:
    reset = reset_for_repost(_record())
    assert reset is not None
    assert (reset.status, reset.message_id, reset.channel_id, reset.approval_thread_id) == (
        "planned", None, "111", "111",
    )


@pytest.mark.parametrize("status", ["planned", "approved", "written", "abandoned"])
def test_reset_for_repost_leaves_every_other_status_alone(status: str) -> None:
    assert reset_for_repost(_record(status=status)) is None


def test_repost_posted_deletes_old_cards_and_resets_only_posted_records(tmp_path: Path) -> None:
    posted = _record()
    planned = _record(recording_id="rec-b", status="planned", message_id=None, channel_id="")
    path = _state_file(tmp_path, posted, planned)
    transport = _Transport()
    assert repost_posted(PlaudSyncStore(path), transport) == ("rec-a",)
    assert transport.deleted == [("111", "m-a")]
    after = load_state(path).records
    assert (after["rec-a"].status, after["rec-a"].message_id) == ("planned", None)
    assert after["rec-a"].approval_thread_id == "111"
    assert after["rec-b"] == planned


def test_repost_posted_tolerates_a_card_that_is_already_gone(tmp_path: Path) -> None:
    path = _state_file(tmp_path, _record())
    assert repost_posted(PlaudSyncStore(path), _Transport({"m-a": 404})) == ("rec-a",)
    assert load_state(path).records["rec-a"].message_id is None


def test_repost_posted_keeps_the_record_when_deletion_fails(tmp_path: Path) -> None:
    path = _state_file(tmp_path, _record())
    assert repost_posted(PlaudSyncStore(path), _Transport({"m-a": 500})) == ()
    assert load_state(path).records["rec-a"] == _record()


def _load_watch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("plaud_sync_watch_under_test", _WATCH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _quiet_watch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[ModuleType, list[str]]:
    watch = _load_watch()
    calls: list[str] = []
    empty = ResolveResult(PlaudSyncState(1, None, {}), (), (), ())

    def _tick(now: object) -> ResolveResult:
        calls.append("tick")
        return empty

    def _repost() -> tuple[str, ...]:
        calls.append("repost")
        return ("rec-a",)

    monkeypatch.setattr(watch, "run_once", _tick)
    monkeypatch.setattr(watch, "_repost_posted", _repost)
    monkeypatch.setattr(watch, "_load_env_secrets", lambda: None)
    monkeypatch.setattr(watch, "LOCK_PATH", tmp_path / "watch.lock")
    return watch, calls


def test_repost_flag_runs_tick_reset_tick_under_one_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    watch, calls = _quiet_watch(monkeypatch, tmp_path)
    assert watch.main(["--repost-posted"]) == 0
    assert calls == ["tick", "repost", "tick"]
    assert "reposted=1" in capsys.readouterr().out


def test_plain_tick_never_reposts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    watch, calls = _quiet_watch(monkeypatch, tmp_path)
    assert watch.main([]) == 0
    assert calls == ["tick"]


# --- 이미 끝난 녹음을 다시 처리한다 (소유자 지시 2026-09-06) ------------------


def test_reset_for_reprocess_sends_a_finished_record_back_to_transcription() -> None:
    """`written` 은 되돌릴 수 없는 종결 상태였다 — 그래서 낡은 코드가 만든 노트가 영원히 남는다.

    2026-09-06 실측: 2026-09-04 노트는 요약이 비었고 전사본은 낱말이 조각나 있었다. 둘 다
    그 뒤에 고쳤지만 그 노트를 다시 만들 길이 없었다. 노트 경로는 녹음 id 에서 나오므로
    다시 처리해 승인받으면 **같은 파일**이 덮어써진다.
    """
    finished = _record(
        status="written",
        approved_at="2026-09-06T01:34:10Z",
        written_at="2026-09-06T01:35:00Z",
        transcribe_attempts=5,
        next_transcribe_at="2026-09-07T00:00:00Z",
        last_block_reason="클라우드 폴백 보류",
    )

    reset = reset_for_reprocess(finished)

    assert reset is not None
    assert reset.status == "transcribing"
    assert (reset.message_id, reset.approved_at, reset.written_at) == (None, None, None)
    assert (reset.transcribe_attempts, reset.next_transcribe_at) == (0, None)
    assert reset.last_block_reason is None
    # 새 카드는 소유자가 이미 가진 스레드로 간다. action_hash 는 새 본문이 정하므로 건드리지 않는다.
    assert reset.approval_thread_id == "111"
    assert reset.action_hash == finished.action_hash


def test_reset_for_reprocess_also_recovers_an_abandoned_recording() -> None:
    """`abandoned` 은 '포기했다' 이지 '다시 못 한다' 가 아니다."""
    assert reset_for_reprocess(_record(status="abandoned")) is not None


@pytest.mark.parametrize("status", ["transcribing", "planned", "posted", "approved"])
def test_reset_for_reprocess_leaves_a_live_record_alone(status: str) -> None:
    """진행 중인 요청을 되돌리면 소유자가 보고 있는 카드가 상태에서 떨어져 나간다."""
    assert reset_for_reprocess(_record(status=status)) is None


def test_reprocess_flips_only_the_named_record_and_keeps_the_old_card(tmp_path: Path) -> None:
    """옛 카드는 지우지 않는다 — 소유자가 실제로 누른 결정의 기록이다."""
    finished = _record(status="written", approved_at="2026-09-06T01:34:10Z")
    other = _record(recording_id="rec-b", status="posted")
    path = _state_file(tmp_path, finished, other)
    store = PlaudSyncStore(path)

    assert reprocess(store, "rec-a") is True

    after = load_state(path).records
    assert after["rec-a"].status == "transcribing"
    assert after["rec-a"].message_id is None
    assert after["rec-b"] == other


def test_reprocess_refuses_an_unknown_id_and_a_live_one(tmp_path: Path) -> None:
    path = _state_file(tmp_path, _record(status="posted"))
    store = PlaudSyncStore(path)

    assert reprocess(store, "rec-missing") is False
    assert reprocess(store, "rec-a") is False
    assert load_state(path).records["rec-a"].status == "posted"


def test_reprocess_flag_queues_the_recording_before_the_tick(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    watch, calls = _quiet_watch(monkeypatch, tmp_path)

    def _queue(recording_id: str) -> bool:
        calls.append(f"reprocess:{recording_id}")
        return True

    monkeypatch.setattr(watch, "_reprocess", _queue)

    assert watch.main(["--reprocess", "rec-a"]) == 0

    assert calls == ["reprocess:rec-a", "tick"]
    assert "reprocess rec-a queued" in capsys.readouterr().out


def test_reprocess_flag_without_an_id_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    watch, _calls = _quiet_watch(monkeypatch, tmp_path)
    assert watch.main(["--reprocess"]) == 1
