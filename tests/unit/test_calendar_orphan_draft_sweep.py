"""고아 캘린더 초안 청소 — post-confirm 없이 남은 pending 초안의 자동 폐기.

draft-create 와 post-confirm 은 별개 단계라, 승인 DM 게시가 누락된 초안은
pending-confirms 원장에 없어 워처의 어떤 경로도 다시 보지 않았다(2026-07~08
실측 33건 누적, 전부 행사일 경과). 이 스위트는 sweep_orphan_drafts 가
게시된 확인에 묶인 초안을 건드리지 않고, 24시간 유예가 지난 고아만 기존
discard 경로로 폐기하며, 실패가 tick 을 죽이지 않음을 고정한다.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "calendar" / "scripts"
os.environ["CALENDAR_SCRIPTS"] = str(_SCRIPTS)
sys.path.insert(0, str(_SCRIPTS))

_pending = import_module("calendar_pending")
PendingConfirm = _pending.PendingConfirm

_NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)


def _load_watch_module():
    spec = importlib.util.spec_from_file_location(
        "calendar_confirm_reaction_watch_orphan", _SCRIPTS / "confirm_reaction_watch.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Commands:
    def __init__(self, fail_for: frozenset[str] = frozenset()) -> None:
        self.discarded: list[str] = []
        self.fail_for = fail_for

    def confirm(self, entry, owner_id) -> None:  # pragma: no cover - not used here
        raise AssertionError("sweep must never confirm")

    def discard(self, draft_id: str) -> None:
        if draft_id in self.fail_for:
            raise RuntimeError("discard rejected")
        self.discarded.append(draft_id)


class _Discord:
    def __init__(self) -> None:
        self.dms: list[str] = []

    def message_content(self, entry):  # pragma: no cover - not used here
        return None

    def reaction_users(self, entry, emoji):  # pragma: no cover - not used here
        return ()

    def send_owner_dm(self, content: str) -> None:
        self.dms.append(content)


def _draft(draft_id: str, *, created: datetime, status: str = "pending") -> dict[str, str]:
    return {
        "id": draft_id,
        "action": "create",
        "status": status,
        "created": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": "0" * 64,
    }


def _entry(draft_id: str) -> PendingConfirm:
    return PendingConfirm(
        draft_id=draft_id,
        sha256="0" * 64,
        dm_channel_id="channel",
        dm_message_id="message",
        created=_NOW,
    )


def test_sweeps_orphan_older_than_expiry() -> None:
    watch = _load_watch_module()
    commands, discord = _Commands(), _Discord()
    records = [_draft("aaaa11", created=_NOW - timedelta(hours=25))]
    swept = watch.sweep_orphan_drafts(
        (), commands=commands, discord=discord, now=_NOW, list_drafts=lambda: records
    )
    assert swept == ("aaaa11",)
    assert commands.discarded == ["aaaa11"]
    assert len(discord.dms) == 1
    assert "aaaa11" in discord.dms[0]
    assert "게시되지 않은" in discord.dms[0]


def test_keeps_fresh_orphan_inside_grace() -> None:
    watch = _load_watch_module()
    commands, discord = _Commands(), _Discord()
    records = [_draft("bbbb22", created=_NOW - timedelta(hours=23))]
    swept = watch.sweep_orphan_drafts(
        (), commands=commands, discord=discord, now=_NOW, list_drafts=lambda: records
    )
    assert swept == ()
    assert commands.discarded == []
    assert discord.dms == []


def test_keeps_draft_bound_to_posted_confirmation() -> None:
    watch = _load_watch_module()
    commands, discord = _Commands(), _Discord()
    records = [_draft("cccc33", created=_NOW - timedelta(days=30))]
    swept = watch.sweep_orphan_drafts(
        (_entry("cccc33"),), commands=commands, discord=discord, now=_NOW,
        list_drafts=lambda: records,
    )
    assert swept == ()
    assert commands.discarded == []


def test_keeps_non_pending_and_undatable_records() -> None:
    watch = _load_watch_module()
    commands, discord = _Commands(), _Discord()
    executed = _draft("dddd44", created=_NOW - timedelta(days=9), status="executed")
    undatable = _draft("eeee55", created=_NOW - timedelta(days=9))
    undatable["created"] = "not-a-timestamp"
    dateless = _draft("ffff66", created=_NOW - timedelta(days=9))
    del dateless["created"]
    swept = watch.sweep_orphan_drafts(
        (), commands=commands, discord=discord, now=_NOW,
        list_drafts=lambda: [executed, undatable, dateless],
    )
    assert swept == ()
    assert commands.discarded == []


def test_discard_failure_is_isolated_per_draft() -> None:
    watch = _load_watch_module()
    commands, discord = _Commands(fail_for=frozenset({"aaaa11"})), _Discord()
    records = [
        _draft("aaaa11", created=_NOW - timedelta(days=2)),
        _draft("bbbb22", created=_NOW - timedelta(days=2)),
    ]
    swept = watch.sweep_orphan_drafts(
        (), commands=commands, discord=discord, now=_NOW, list_drafts=lambda: records
    )
    assert swept == ("bbbb22",)
    assert commands.discarded == ["bbbb22"]
    assert len(discord.dms) == 1


def test_scan_failure_returns_empty_without_raising() -> None:
    watch = _load_watch_module()

    def _boom() -> list[dict[str, str]]:
        raise OSError("gate unavailable")

    swept = watch.sweep_orphan_drafts(
        (), commands=_Commands(), discord=_Discord(), now=_NOW, list_drafts=_boom
    )
    assert swept == ()


def test_run_once_wires_the_sweep(monkeypatch, tmp_path) -> None:
    watch = _load_watch_module()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        watch, "sweep_orphan_drafts", lambda snapshot, **kwargs: calls.append((snapshot, kwargs))
    )
    store = _pending.PendingConfirmStore(tmp_path / "pending-confirms.jsonl")
    watch.run_once(
        store=store,
        owner_id="owner",
        discord=_Discord(),
        commands=_Commands(),
        draft_sha256=lambda draft_id: "0" * 64,
        now=_NOW,
    )
    assert len(calls) == 1
    assert calls[0][0] == ()
    assert set(calls[0][1]) == {"commands", "discord", "now"}
