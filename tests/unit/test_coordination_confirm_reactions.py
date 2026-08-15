from __future__ import annotations

import stat
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "coordination" / "scripts"))

import confirm_reaction_watch as watch  # noqa: E402
from coordination_pending import PendingConfirm, PendingConfirmStore  # noqa: E402


@dataclass
class FakeDiscord:
    reactions: dict[str, tuple[dict[str, str | bool], ...]]
    sent_messages: list[str] = field(default_factory=list)
    notify_error: bool = False

    def message_content(self, _entry: PendingConfirm) -> str:
        return "coordination confirmation sha256:sha-123"

    def reaction_users(
        self, _entry: PendingConfirm, emoji: str
    ) -> tuple[dict[str, str | bool], ...]:
        return self.reactions.get(emoji, ())

    def send_owner_dm(self, content: str) -> None:
        if self.notify_error:
            raise watch.ConfirmWatchError("owner DM notification failed")
        self.sent_messages.append(content)


@dataclass
class FakeCommands:
    finalized: list[PendingConfirm] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)

    def finalize(self, entry: PendingConfirm) -> None:
        self.finalized.append(entry)

    def discard(self, draft_id: str) -> None:
        self.discarded.append(draft_id)


def _entry(*, created: datetime | None = None) -> PendingConfirm:
    return PendingConfirm(
        draft_id="abc123",
        sha256="sha-123",
        dm_channel_id="dm-1",
        dm_message_id="msg-1",
        slot="2026-07-18T09:00:00+09:00",
        summary="피어 미팅",
        correlation="coord-123",
        duration_min=30,
        created=created or datetime(2026, 7, 17, tzinfo=UTC),
    )


def _run(
    tmp_path: Path,
    reactions: dict[str, tuple[dict[str, str | bool], ...]],
    *,
    created: datetime | None = None,
    notify_error: bool = False,
) -> tuple[PendingConfirmStore, FakeDiscord, FakeCommands]:
    store = PendingConfirmStore(tmp_path / "pending-confirms.jsonl")
    store.append(_entry(created=created))
    discord = FakeDiscord(reactions, notify_error=notify_error)
    commands = FakeCommands()
    watch.run_once(
        store=store,
        owner_id="cha-owner",
        discord=discord,
        commands=commands,
        draft_sha256=lambda _draft_id: "sha-123",
        now=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
    )
    return store, discord, commands


def test_finalize_once_when_owner_has_only_approve_reaction(tmp_path: Path) -> None:
    # Given
    store, _discord, commands = _run(tmp_path, {"✅": ({"id": "cha-owner", "bot": False},)})

    # When / Then
    assert [entry.draft_id for entry in commands.finalized] == ["abc123"]
    assert commands.discarded == []
    assert store.load() == ()


def test_discard_without_finalize_when_owner_has_cancel_reaction(tmp_path: Path) -> None:
    # Given
    store, discord, commands = _run(tmp_path, {"⛔": ({"id": "cha-owner", "bot": False},)})

    # When / Then
    assert commands.finalized == []
    assert commands.discarded == ["abc123"]
    assert discord.sent_messages == ["취소됨"]
    assert store.load() == ()


def test_cancel_wins_when_owner_has_both_reactions(tmp_path: Path) -> None:
    # Given
    reactions = {
        "✅": ({"id": "cha-owner", "bot": False},),
        "⛔": ({"id": "cha-owner", "bot": False},),
    }
    _store, _discord, commands = _run(tmp_path, reactions)

    # When / Then
    assert commands.finalized == []
    assert commands.discarded == ["abc123"]


def test_ignore_bot_reaction(tmp_path: Path) -> None:
    # Given
    store, _discord, commands = _run(tmp_path, {"✅": ({"id": "cha-owner", "bot": True},)})

    # When / Then
    assert commands.finalized == []
    assert commands.discarded == []
    assert len(store.load()) == 1


def test_ignore_non_owner_reaction(tmp_path: Path) -> None:
    # Given
    store, _discord, commands = _run(tmp_path, {"✅": ({"id": "other-user", "bot": False},)})

    # When / Then
    assert commands.finalized == []
    assert commands.discarded == []
    assert len(store.load()) == 1


def test_expired_entry_is_discarded_and_removed(tmp_path: Path) -> None:
    # Given
    expired = datetime(2026, 7, 15, 11, 59, tzinfo=UTC)
    store, discord, commands = _run(tmp_path, {}, created=expired)

    # When / Then
    assert commands.finalized == []
    assert commands.discarded == ["abc123"]
    assert discord.sent_messages == ["확정 시간이 지나 취소되었습니다"]
    assert store.load() == ()


def test_pending_store_is_owner_readable_only(tmp_path: Path) -> None:
    # Given
    store = PendingConfirmStore(tmp_path / "pending-confirms.jsonl")

    # When
    store.append(_entry())

    # Then
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600



def test_cancel_discard_purges_entry_when_owner_notify_fails(tmp_path: Path) -> None:
    # Given the owner's ⛔ where the follow-up DM notification fails
    store, _discord, commands = _run(
        tmp_path, {"⛔": ({"id": "cha-owner", "bot": False},)}, notify_error=True
    )

    # When / Then — the successful discard is not rolled back by the notify failure
    assert commands.discarded == ["abc123"]
    assert store.load() == ()