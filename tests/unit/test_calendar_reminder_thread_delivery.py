"""Calendar reminders retain the approval channel and server-aware source link."""
from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Final
from urllib.error import URLError

import pytest

from automation.interop import approval_reminder as reminder
from automation.interop.approval_reminder_config import ApprovalReminderConfig
from automation.interop.approval_surface import ApprovalKind
from skills.calendar.scripts.calendar_pending import PendingConfirm, PendingConfirmStore

_SCRIPTS: Final = Path(__file__).resolve().parents[2] / "skills/calendar/scripts"
_NOW: Final = datetime(2026, 9, 11, 12, tzinfo=UTC)
_THREAD: Final = "1500000000000000001"
_MESSAGE: Final = "1500000000000000003"
_GUILD: Final = "1500000000000000009"


@pytest.fixture
def watch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("CALENDAR_SCRIPTS", str(_SCRIPTS))
    monkeypatch.syspath_prepend(str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "calendar_reminder_thread_watch", _SCRIPTS / "confirm_reaction_watch.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True, slots=True)
class _Discord:
    """Accumulate every transport call without opening a socket."""

    guild_id: str | None = _GUILD
    calls: list[tuple[str, str]] = field(default_factory=list)
    posts: list[tuple[str, str]] = field(default_factory=list)
    dms: list[str] = field(default_factory=list)

    def message_content(self, entry: PendingConfirm) -> str:
        self.calls.append(("message", entry.dm_message_id))
        return f"sha256:{entry.sha256}"

    def reaction_users(
        self, entry: PendingConfirm, emoji: str
    ) -> tuple[Mapping[str, str | bool], ...]:
        self.calls.append(("reactions", emoji))
        return ()

    def fetch_channel(self, channel_id: str) -> Mapping[str, str | None]:
        self.calls.append(("channel", channel_id))
        return {"id": channel_id, "guild_id": self.guild_id}

    def post_message(self, channel_id: str, content: str) -> None:
        self.calls.append(("post", channel_id))
        self.posts.append((channel_id, content))

    def send_owner_dm(self, content: str) -> None:
        self.calls.append(("dm", "owner"))
        self.dms.append(content)


@dataclass(frozen=True, slots=True)
class _Commands:
    def confirm(self, entry: PendingConfirm, owner_id: str) -> None:
        pytest.fail("pending approval must not execute")

    def discard(self, draft_id: str) -> None:
        pytest.fail("pending approval must not be discarded")


@pytest.mark.parametrize("guild_id", [_GUILD, None])
def test_posts_in_request_channel_when_reminder_is_due(
    watch: ModuleType, tmp_path: Path, guild_id: str | None
) -> None:
    # Given: a bound, pending card exactly three hours old, with private local state.
    entry = PendingConfirm("qa0001", "a" * 64, _THREAD, _MESSAGE, _NOW - timedelta(hours=3))
    store = PendingConfirmStore(tmp_path / "pending.jsonl")
    store.append(entry)
    discord = _Discord(guild_id)

    # When: the real watcher probes, claims and dispatches the due reminder.
    retained = watch._process_entries(
        (entry,), store, "owner", discord, _Commands(), lambda _: entry.sha256, _NOW,
        reminder_config=ApprovalReminderConfig(),
    )

    # Then: shipped reminder copy goes only to the authorized card channel, never a DM.
    link = f"https://discord.com/channels/{guild_id}/{_THREAD}/{_MESSAGE}" if guild_id else None
    expected = reminder.compose_reminder(
        reminder.ApprovalReminder(ApprovalKind.CALENDAR, timedelta(hours=3), link)
    )
    assert discord.posts == [(_THREAD, expected)]
    assert discord.dms == []
    if link is not None:
        assert link in discord.posts[0][1]
    else:
        assert "discord.com" not in discord.posts[0][1]
    assert retained == (entry,)


@pytest.mark.parametrize("payload", [{}, {"guild_id": ""}, {"guild_id": None}])
def test_requires_explicit_dm_space_when_channel_has_no_guild(payload: Mapping[str, str | None]) -> None:
    # Given: a DM channel response has no non-empty guild id.
    resolve = reminder.channel_guild_resolver(lambda _: payload)
    # When
    guild_id = resolve(_THREAD)
    # Missing guild alone is unknown; an explicitly known DM retains its supported link.
    assert guild_id is None
    assert reminder.discord_message_link(reminder.DiscordSource(_THREAD, _MESSAGE, guild_id)).url is None
    assert reminder.discord_message_link(reminder.DiscordSource(_THREAD, _MESSAGE, guild_id, space="dm")).url == (
        f"https://discord.com/channels/@me/{_THREAD}/{_MESSAGE}"
    )


def test_fetches_once_per_channel_when_resolver_is_reused() -> None:
    # Given: one resolver is shared by several reminders in a tick.
    calls: list[str] = []

    def fetch(channel_id: str) -> Mapping[str, str]:
        calls.append(channel_id)
        return {"guild_id": _GUILD} if channel_id == _THREAD else {}

    resolve = reminder.channel_guild_resolver(fetch)
    # When: both guild and DM channels are requested repeatedly.
    results = [resolve(channel) for channel in (_THREAD, _MESSAGE, _THREAD, _MESSAGE)]
    # Then: None is cached too, and a different channel gets its own lookup.
    assert results == [_GUILD, None, _GUILD, None]
    assert calls == [_THREAD, _MESSAGE]


def test_propagates_failure_when_channel_lookup_fails() -> None:
    # Given: Discord cannot supply the channel metadata.
    error = URLError("channel unavailable")

    def fetch(channel_id: str) -> Mapping[str, str]:
        raise error

    resolve = reminder.channel_guild_resolver(fetch)
    # When / Then: no invented DM fallback hides a transport failure.
    with pytest.raises(URLError) as caught:
        resolve(_THREAD)
    assert caught.value is error
