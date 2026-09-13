"""요청 스레드가 없는 캘린더 통지의 목적지 — 소유자 DM 이 아니라 지정 통지 채널.

2026-09-10 소유자 관측: 승인 카드가 게시되지 않은 초안 3건이 24시간 뒤 자동 폐기됐는데,
그 정리 통지가 소유자 DM 으로만 갔다. 지시는 "이 내용도 #notifications 에서 나오도록 해".

여기서 고정하는 것은 목적지 하나다: 승인 스레드도 origin 채널도 없는 결과·정리 통지는
`automation.owner_notice.notify_owner` 파사드를 지나 `OWNER_NOTICE_CHANNEL_ID` 채널로
간다. 채널이 미설정이면 파사드가 소유자 DM 으로 되돌리므로 레거시 설치는 무영향이고,
DM 오픈은 여전히 파사드 안에서만 일어난다(ON-2/ON-3).

스레드가 있는 건은 이 규칙의 대상이 아니다 — 그것은 「결과 통지 원채널 스레드 규칙」이
소유하며 여기서 함께 고정해 두 규칙이 서로를 덮지 않게 한다.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from automation import owner_notice

_REPO: Final = Path(__file__).resolve().parents[2]
_SCRIPTS: Final = _REPO / "skills" / "calendar" / "scripts"
os.environ["CALENDAR_SCRIPTS"] = str(_SCRIPTS)
sys.path.insert(0, str(_SCRIPTS))

_NOW: Final = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
_CHANNEL: Final = "1500000000000000001"


def _load_watch_module():
    spec = importlib.util.spec_from_file_location(
        "calendar_confirm_reaction_watch_notice", _SCRIPTS / "confirm_reaction_watch.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Discord:
    """워처의 Discord 클라이언트 — 이 경로에서는 한 번도 쓰이면 안 된다."""

    def __init__(self) -> None:
        self.dms: list[str] = []

    def message_content(self, entry):  # pragma: no cover - 이 경로는 쓰지 않는다
        return None

    def reaction_users(self, entry, emoji):  # pragma: no cover - 이 경로는 쓰지 않는다
        return ()

    def send_owner_dm(self, content: str) -> None:
        self.dms.append(content)


class _Commands:
    def __init__(self) -> None:
        self.discarded: list[str] = []

    def confirm(self, entry, owner_id) -> None:  # pragma: no cover - sweep 은 실행하지 않는다
        raise AssertionError("sweep must never confirm")

    def discard(self, draft_id: str) -> None:
        self.discarded.append(draft_id)


def _draft(draft_id: str, *, created: datetime, **extra: str) -> dict[str, str]:
    return {
        "id": draft_id,
        "action": "create",
        "status": "pending",
        "created": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": "0" * 64,
        **extra,
    }


@pytest.fixture
def notice_channel(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """지정 통지 채널이 있는 설치를 흉내내고 전송 계층에서 (채널, 본문) 을 잡는다."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-test-token")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", _CHANNEL)
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        owner_notice, "send_notice", lambda token, channel, body: sent.append((channel, body))
    )
    monkeypatch.setattr(
        owner_notice,
        "owner_dm_channel",
        lambda token, owner_id: pytest.fail("채널이 지정되면 DM 을 열지 않는다"),
    )
    return sent


def test_a_threadless_result_notice_lands_in_the_notice_channel(
    notice_channel: list[tuple[str, str]],
) -> None:
    # Given: 승인 스레드도 origin 채널도 없는 결과 통지
    watch = _load_watch_module()
    discord = _Discord()

    # When
    watch._notify_result(discord, {"id": "aaaa11", "action": "create"}, "⛔ 취소 (draft aaaa11)")

    # Then: 파사드가 지정 채널로 보냈고 DM 은 열리지 않았다
    assert len(notice_channel) == 1
    assert notice_channel[0][0] == _CHANNEL
    assert "aaaa11" in notice_channel[0][1]
    assert len(notice_channel[0][1].splitlines()) == 5
    assert discord.dms == []


def test_b_orphan_cleanup_notice_reaches_the_notice_channel_end_to_end(
    notice_channel: list[tuple[str, str]],
) -> None:
    # Given: 카드 없이 24시간이 지난 고아 초안 — 소유자가 실제로 본 그 경로
    watch = _load_watch_module()
    commands, discord = _Commands(), _Discord()
    records = [_draft("aaaa11", created=_NOW - timedelta(hours=25))]

    # When
    swept = watch.sweep_orphan_drafts(
        (), commands=commands, discord=discord, now=_NOW, list_drafts=lambda: records
    )

    # Then
    assert swept == ("aaaa11",)
    assert commands.discarded == ["aaaa11"]
    assert discord.dms == []
    assert len(notice_channel) == 1
    channel, body = notice_channel[0]
    assert channel == _CHANNEL
    assert "aaaa11" in body
    assert "게시되지 않은" in body


def test_c_without_a_configured_channel_the_facade_still_reaches_the_owner_dm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """레거시 설치(통지 채널 미설정)는 종전대로 소유자 DM 에 닿는다 — 파사드 안에서."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-test-token")
    monkeypatch.delenv("OWNER_NOTICE_CHANNEL_ID", raising=False)
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda home=None: "")
    monkeypatch.setattr(owner_notice, "_config_owner_id", lambda: "42")
    monkeypatch.setattr(owner_notice, "owner_dm_channel", lambda token, owner_id: "dm-1")
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        owner_notice, "send_notice", lambda token, channel, body: sent.append((channel, body))
    )
    watch = _load_watch_module()

    watch._notify_result(_Discord(), {"id": "bbbb22", "action": "create"}, "🧹 정리")

    assert len(sent) == 1
    assert sent[0][0] == "dm-1"
    assert "bbbb22" in sent[0][1]
    assert len(sent[0][1].splitlines()) == 5


def test_d_a_request_with_its_own_thread_is_untouched_by_this_rule(
    notice_channel: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """스레드가 있으면 결과는 그 스레드로 간다 — 통지 채널로 새지 않는다."""
    watch = _load_watch_module()
    threaded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        watch, "_notify_thread", lambda record, content, outcome="": threaded.append(
            (str(record["id"]), content)
        )
    )

    watch._notify_result(
        _Discord(), {"id": "cccc33", "action": "create", "approval_thread_id": "9"}, "✅ 완료"
    )

    assert threaded == [("cccc33", "✅ 완료")]
    assert notice_channel == []
