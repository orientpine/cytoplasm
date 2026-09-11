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

import pytest

from automation import owner_notice

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "calendar" / "scripts"
os.environ["CALENDAR_SCRIPTS"] = str(_SCRIPTS)
sys.path.insert(0, str(_SCRIPTS))

_pending = import_module("calendar_pending")
PendingConfirm = _pending.PendingConfirm

_NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def notices(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """정리 통지의 목적지는 워처가 아니라 `owner_notice` 파사드가 정한다(2026-09-10).

    설치에 `owner_notice_channel_id`(#notifications)가 있으면 그 채널로, 없으면 소유자
    DM 으로 간다. 여기서는 파사드를 잡아 **무엇이 나갔는지**만 본다 — 목적지 해석은
    `test_calendar_owner_notice_routing.py` 가 고정한다.
    """
    delivered: list[str] = []
    monkeypatch.setattr(
        owner_notice, "notify_owner", lambda notice: delivered.append(notice) is None
    )
    return delivered


@pytest.fixture(autouse=True)
def posted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """카드 게시 백스톱이 부르는 기존 승인 게이트 — 단위 테스트는 Discord 로 나가지 않는다.

    autouse 인 이유는 안전이다: 어떤 케이스가 게시 분기에 들어가도 실제
    `request_confirmation` 이 불리지 않는다.
    """
    cards: list[str] = []
    approval = import_module("calendar_approval")
    monkeypatch.setattr(
        approval, "request_confirmation", lambda record: cards.append(str(record["id"]))
    )
    return cards


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


def test_sweeps_orphan_older_than_expiry(notices: list[str]) -> None:
    watch = _load_watch_module()
    commands, discord = _Commands(), _Discord()
    records = [_draft("aaaa11", created=_NOW - timedelta(hours=25))]
    swept = watch.sweep_orphan_drafts(
        (), commands=commands, discord=discord, now=_NOW, list_drafts=lambda: records
    )
    assert swept == ("aaaa11",)
    assert commands.discarded == ["aaaa11"]
    assert discord.dms == []  # 워처가 직접 DM 을 열지 않는다 — 목적지는 파사드 몫이다
    assert len(notices) == 1
    assert "aaaa11" in notices[0]
    assert "게시되지 않은" in notices[0]


def test_keeps_fresh_orphan_inside_grace(notices: list[str], posted: list[str]) -> None:
    """유예 안의 고아는 폐기하지 않는다 — 대신 그 자리에서 누락된 카드를 올린다."""
    watch = _load_watch_module()
    commands, discord = _Commands(), _Discord()
    records = [_draft("bbbb22", created=_NOW - timedelta(hours=23))]
    swept = watch.sweep_orphan_drafts(
        (), commands=commands, discord=discord, now=_NOW, list_drafts=lambda: records
    )
    assert swept == ()
    assert commands.discarded == []
    assert discord.dms == []
    assert notices == []
    assert posted == ["bbbb22"]


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


def test_discard_failure_is_isolated_per_draft(notices: list[str]) -> None:
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
    assert len(notices) == 1


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


def test_a_missing_card_is_posted_instead_of_waiting_out_the_expiry(
    posted: list[str], notices: list[str]
) -> None:
    """카드 없는 초안은 24시간을 기다렸다 폐기하는 대신 그 자리에서 카드를 올린다.

    2026-09-10 실측 근거: 노드 agent 계정의 승인 lease 는 09-06 이후 하나도 늘지 않았고
    posting-journal 은 비어 있는데 pending 초안은 계속 쌓였다 — `post-confirm` 이 아예
    불리지 않았다는 뜻이다(draft-create 와는 별개의 CLI 호출이고 2단계를 강제하는 코드가
    없다). 폐기 통지만으로는 소유자의 요청이 영영 실행되지 않는다.
    """
    watch = _load_watch_module()
    commands, discord = _Commands(), _Discord()
    records = [_draft("aaaa11", created=_NOW - timedelta(hours=1))]

    swept = watch.sweep_orphan_drafts(
        (), commands=commands, discord=discord, now=_NOW, list_drafts=lambda: records
    )

    assert swept == ()
    assert commands.discarded == []
    assert posted == ["aaaa11"]
    assert notices == []


def test_a_brand_new_draft_is_left_to_the_agents_own_post_confirm(posted: list[str]) -> None:
    """유예 안에서는 손대지 않는다 — 정상 경로(에이전트 자신의 post-confirm)가 이긴다."""
    watch = _load_watch_module()
    commands = _Commands()
    records = [_draft("bbbb22", created=_NOW - timedelta(seconds=30))]

    swept = watch.sweep_orphan_drafts(
        (), commands=commands, discord=_Discord(), now=_NOW, list_drafts=lambda: records
    )

    assert swept == ()
    assert posted == []


def test_a_failed_card_post_is_isolated_and_never_discards(
    monkeypatch: pytest.MonkeyPatch, notices: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """게시 실패는 그 초안 하나만 건너뛴다 — 폐기하지 않고 다음 tick 이 다시 본다."""
    watch = _load_watch_module()
    approval = import_module("calendar_approval")

    def refuse(record: dict[str, str]) -> None:
        raise RuntimeError("approval surface unavailable")

    monkeypatch.setattr(approval, "request_confirmation", refuse)
    commands = _Commands()
    records = [_draft("cccc33", created=_NOW - timedelta(hours=2))]

    swept = watch.sweep_orphan_drafts(
        (), commands=commands, discord=_Discord(), now=_NOW, list_drafts=lambda: records
    )

    assert swept == ()
    assert commands.discarded == []
    assert notices == []
    assert "missing card post failed draft=cccc33" in capsys.readouterr().err


def test_an_expired_orphan_is_discarded_rather_than_posted(posted: list[str]) -> None:
    """24시간을 넘긴 초안은 게시가 아니라 폐기다 — 두 분기가 겹치지 않는다."""
    watch = _load_watch_module()
    commands = _Commands()
    records = [_draft("dddd44", created=_NOW - timedelta(hours=25))]

    swept = watch.sweep_orphan_drafts(
        (), commands=commands, discord=_Discord(), now=_NOW, list_drafts=lambda: records
    )

    assert swept == ("dddd44",)
    assert posted == []
