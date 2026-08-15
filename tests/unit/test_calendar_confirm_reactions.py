from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "calendar" / "scripts"
os.environ["CALENDAR_SCRIPTS"] = str(_SCRIPTS)
sys.path.insert(0, str(_SCRIPTS))

calendar_cli = import_module("calendar_cli")
calendar_confirm = import_module("calendar_confirm")
calendar_gate = import_module("calendar_gate")
_pending = import_module("calendar_pending")
PendingConfirm = _pending.PendingConfirm
PendingConfirmStore = _pending.PendingConfirmStore
OWNER_DM_CHANNEL_ID = "1526487935975952385"


def _load_watch_module():
    spec = importlib.util.spec_from_file_location(
        "calendar_confirm_reaction_watch", _SCRIPTS / "confirm_reaction_watch.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


watch = _load_watch_module()


@dataclass
class FakeDiscord:
    reactions: dict[str, tuple[dict[str, str | bool], ...]]
    content: str = "calendar confirmation sha256:sha-123"
    sent_messages: list[str] = field(default_factory=list)
    message_reads: int = 0
    reaction_reads: list[str] = field(default_factory=list)
    notify_error: bool = False

    def message_content(self, _entry: PendingConfirm) -> str:
        self.message_reads += 1
        return self.content

    def reaction_users(
        self, _entry: PendingConfirm, emoji: str
    ) -> tuple[dict[str, str | bool], ...]:
        self.reaction_reads.append(emoji)
        return self.reactions.get(emoji, ())

    def send_owner_dm(self, content: str) -> None:
        if self.notify_error:
            raise watch.ConfirmWatchError("owner DM notification failed")
        self.sent_messages.append(content)


@dataclass
class FakeCommands:
    confirmed: list[PendingConfirm] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)

    def confirm(self, entry: PendingConfirm, _owner_id: str) -> None:
        self.confirmed.append(entry)

    def discard(self, draft_id: str) -> None:
        self.discarded.append(draft_id)


def _entry(*, created: datetime | None = None) -> PendingConfirm:
    return PendingConfirm(
        draft_id="abc123",
        sha256="sha-123",
        dm_channel_id="dm-1",
        dm_message_id="msg-1",
        created=created or datetime(2026, 7, 17, tzinfo=UTC),
    )


def _run(
    tmp_path: Path,
    reactions: dict[str, tuple[dict[str, str | bool], ...]],
    *,
    created: datetime | None = None,
    content: str = "calendar confirmation sha256:sha-123",
    draft_hash: str = "sha-123",
    notify_error: bool = False,
) -> tuple[PendingConfirmStore, FakeDiscord, FakeCommands]:
    store = PendingConfirmStore(tmp_path / "pending-confirms.jsonl")
    store.append(_entry(created=created))
    discord = FakeDiscord(reactions, content, notify_error=notify_error)
    commands = FakeCommands()
    watch.run_once(
        store=store,
        owner_id="cha-owner",
        discord=discord,
        commands=commands,
        draft_sha256=lambda _draft_id: draft_hash,
        now=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
    )
    return store, discord, commands


def test_confirm_when_owner_has_only_approve_reaction(tmp_path: Path) -> None:
    # Given
    store, _discord, commands = _run(tmp_path, {"✅": ({"id": "cha-owner", "bot": False},)})

    # When / Then
    assert [entry.draft_id for entry in commands.confirmed] == ["abc123"]
    assert commands.discarded == []
    assert store.load() == ()


def test_discard_without_confirm_when_owner_has_cancel_reaction(tmp_path: Path) -> None:
    # Given
    store, discord, commands = _run(tmp_path, {"⛔": ({"id": "cha-owner", "bot": False},)})

    # When / Then
    assert commands.confirmed == []
    assert commands.discarded == ["abc123"]
    assert discord.sent_messages == ["취소됨"]
    assert store.load() == ()


def test_cancel_wins_when_owner_has_both_reactions(tmp_path: Path) -> None:
    # Given
    reactions = {
        "✅": ({"id": "cha-owner", "bot": False},),
        "⛔": ({"id": "cha-owner", "bot": False},),
    }

    # When
    _store, _discord, commands = _run(tmp_path, reactions)

    # Then
    assert commands.confirmed == []
    assert commands.discarded == ["abc123"]


def test_ignore_bot_and_non_owner_reactions(tmp_path: Path) -> None:
    # Given
    reactions = {
        "✅": (
            {"id": "cha-owner", "bot": True},
            {"id": "other-user", "bot": False},
        )
    }

    # When
    store, _discord, commands = _run(tmp_path, reactions)

    # Then
    assert commands.confirmed == []
    assert commands.discarded == []
    assert len(store.load()) == 1


def test_discard_when_confirmation_expires(tmp_path: Path) -> None:
    # Given
    expired = datetime(2026, 7, 15, 11, 59, tzinfo=UTC)

    # When
    store, discord, commands = _run(tmp_path, {}, created=expired)

    # Then
    assert commands.confirmed == []
    assert commands.discarded == ["abc123"]
    assert discord.sent_messages == ["확정 시간이 지나 취소되었습니다"]
    assert store.load() == ()


def test_retain_when_draft_or_dm_hash_binding_mismatches(tmp_path: Path) -> None:
    # Given / When
    draft_store, _draft_discord, draft_commands = _run(
        tmp_path / "draft", {"✅": ({"id": "cha-owner", "bot": False},)}, draft_hash="other"
    )
    dm_store, _dm_discord, dm_commands = _run(
        tmp_path / "dm", {"✅": ({"id": "cha-owner", "bot": False},)}, content="sha256:other"
    )

    # Then
    assert draft_commands.confirmed == [] and draft_commands.discarded == []
    assert dm_commands.confirmed == [] and dm_commands.discarded == []
    assert len(draft_store.load()) == 1
    assert len(dm_store.load()) == 1


def test_pending_store_is_owner_readable_only(tmp_path: Path) -> None:
    # Given
    store = PendingConfirmStore(tmp_path / "pending-confirms.jsonl")

    # When
    store.append(_entry())

    # Then
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_post_confirm_posts_reactions_and_records_bound_pending_entry(tmp_path: Path, monkeypatch) -> None:
    # Given
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("CALENDAR_PENDING_CONFIRMS", str(tmp_path / "pending-confirms.jsonl"))
    draft = calendar_gate.create_draft(
        action="delete", argv=("gws", "calendar", "events", "delete"), calendar_id="primary",
        event_id="evt1", summary="private", start="", end="", channel_id="dm",
    )
    calls: list[tuple[str, str, dict[str, str] | None]] = []

    def fake_api(method: str, path: str, payload: dict[str, str] | None = None):
        calls.append((method, path, payload))
        if path == "/users/@me/channels":
            return {"id": OWNER_DM_CHANNEL_ID}
        if path == f"/channels/{OWNER_DM_CHANNEL_ID}":
            return {"id": OWNER_DM_CHANNEL_ID, "name": "", "recipients": [{"id": "cha-owner"}], "type": 1}
        if path == f"/channels/{OWNER_DM_CHANNEL_ID}/messages":
            return {"id": "msg-1"}
        return None

    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "cha-owner")
    monkeypatch.setattr(calendar_confirm, "_api", fake_api)

    # When
    monkeypatch.setattr(sys, "argv", ["calendar_cli", "post-confirm", "--draft", draft["id"]])
    assert calendar_cli.main() == 0

    # Then
    entry = PendingConfirmStore().load()
    assert entry == (
        PendingConfirm(
            draft_id=draft["id"], sha256=draft["sha256"], dm_channel_id=OWNER_DM_CHANNEL_ID,
            dm_message_id="msg-1", created=entry[0].created,
        ),
    )
    assert [call for call in calls if call[0] == "PUT"] == [
            ("PUT", f"/channels/{OWNER_DM_CHANNEL_ID}/messages/msg-1/reactions/%E2%9C%85/@me", None),
            ("PUT", f"/channels/{OWNER_DM_CHANNEL_ID}/messages/msg-1/reactions/%E2%9B%94/@me", None),
    ]



def test_cancel_discard_purges_entry_when_owner_notify_fails(tmp_path: Path) -> None:
    # Given the owner's ⛔ where the follow-up DM notification fails
    store, _discord, commands = _run(
        tmp_path, {"⛔": ({"id": "cha-owner", "bot": False},)}, notify_error=True
    )

    # When / Then — the successful discard is not rolled back by the notify failure
    assert commands.discarded == ["abc123"]
    assert store.load() == ()


def test_expiry_discard_with_notify_failure_leaves_no_stale_entry_for_next_tick(tmp_path: Path) -> None:
    # Given an expired entry whose cancellation DM notification fails
    expired = datetime(2026, 7, 15, 11, 59, tzinfo=UTC)
    store, _discord, commands = _run(tmp_path, {}, created=expired, notify_error=True)
    assert commands.discarded == ["abc123"]

    # When the next tick runs against a resolver that fails for missing drafts
    second_commands = FakeCommands()

    def missing_draft(_draft_id: str) -> str:
        raise watch.ConfirmWatchError("pending draft is unavailable")

    watch.run_once(
        store=store,
        owner_id="cha-owner",
        discord=FakeDiscord({}),
        commands=second_commands,
        draft_sha256=missing_draft,
        now=datetime(2026, 7, 17, 12, 1, tzinfo=UTC),
    )

    # Then the store stays empty and nothing is re-processed
    assert store.load() == ()
    assert second_commands.discarded == []


def test_cli_discard_purges_pending_confirm_entry(tmp_path: Path, monkeypatch) -> None:
    # Given a draft bound to a pending confirmation entry
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("CALENDAR_PENDING_CONFIRMS", str(tmp_path / "pending-confirms.jsonl"))
    draft = calendar_gate.create_draft(
        action="delete", argv=("gws", "calendar", "events", "delete"), calendar_id="primary",
        event_id="evt1", summary="private", start="", end="", channel_id="dm",
    )
    PendingConfirmStore().append(
        PendingConfirm(
            draft_id=draft["id"], sha256=draft["sha256"], dm_channel_id="dm-1",
            dm_message_id="msg-1", created=datetime(2026, 7, 17, tzinfo=UTC),
        )
    )

    # When
    monkeypatch.setattr(sys, "argv", ["calendar_cli", "discard", "--draft", draft["id"]])
    assert calendar_cli.main() == 0

    # Then the pending entry is purged together with the draft
    assert PendingConfirmStore().load() == ()


def _draft_with_pending(tmp_path: Path, monkeypatch, *, message_id: str = "msg-1"):
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("CALENDAR_PENDING_CONFIRMS", str(tmp_path / "pending-confirms.jsonl"))
    draft = calendar_gate.create_draft(
        action="delete", argv=("gws", "calendar", "events", "delete"), calendar_id="primary",
        event_id="evt1", summary="private", start="", end="", channel_id="dm",
    )
    entry = PendingConfirm(
        draft_id=draft["id"], sha256=draft["sha256"], dm_channel_id="dm-1",
        dm_message_id=message_id, created=datetime.now(UTC),
    )
    PendingConfirmStore().append(entry)
    return draft, entry


def test_watcher_approval_queries_discord_once_then_child_uses_only_bound_artifact(
    tmp_path: Path, monkeypatch
) -> None:
    # Given a real pending draft and an owner approval visible to the watcher
    draft, _entry = _draft_with_pending(tmp_path, monkeypatch)
    discord = FakeDiscord(
        {"✅": ({"id": "cha-owner", "bot": False},)},
        content=f"calendar confirmation sha256:{draft['sha256']}",
    )
    executions: list[str] = []
    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "cha-owner")
    monkeypatch.setattr(
        calendar_confirm, "_api",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate Discord query")),
    )
    monkeypatch.setattr(
        calendar_gate, "execute_draft",
        lambda current, _approval: executions.append(current["id"]) or current["event_id"],
    )
    monkeypatch.setattr(
        calendar_cli.calendar_preflight,
        "read_event",
        lambda _calendar_id, event_id: {"id": event_id, "summary": draft["summary"]},
    )

    class InProcessCommands:
        def confirm(self, current: PendingConfirm, owner: str) -> None:
            authorization = calendar_confirm.create_watcher_authorization(current, owner)
            args = SimpleNamespace(
                draft=current.draft_id, injection_file="", watch_authorization=str(authorization)
            )
            assert calendar_cli.cmd_confirm(args) == 0

        def discard(self, _draft_id: str) -> None:
            raise AssertionError("approval must not discard")

    # When
    watch.run_once(
        store=PendingConfirmStore(), owner_id="cha-owner", discord=discord,
        commands=InProcessCommands(), draft_sha256=lambda _draft_id: draft["sha256"],
        now=datetime.now(UTC),
    )

    # Then: one exact-message read + one read for each reaction; child makes none.
    assert discord.message_reads == 1
    assert discord.reaction_reads == ["⛔", "✅"]
    assert executions == [draft["id"]]
    assert PendingConfirmStore().load() == ()


def test_watcher_authorization_is_one_use_and_cannot_be_replayed(tmp_path: Path, monkeypatch) -> None:
    draft, entry = _draft_with_pending(tmp_path, monkeypatch)
    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "cha-owner")
    authorization = calendar_confirm.create_watcher_authorization(entry, "cha-owner")
    copied_authorization = authorization.with_name("copied.json")
    copied_authorization.write_bytes(authorization.read_bytes())
    copied_authorization.chmod(0o600)
    executions: list[str] = []
    monkeypatch.setattr(
        calendar_gate, "execute_draft",
        lambda current, _approval: executions.append(current["id"]) or current["event_id"],
    )
    monkeypatch.setattr(
        calendar_cli.calendar_preflight,
        "read_event",
        lambda _calendar_id, event_id: {"id": event_id, "summary": draft["summary"]},
    )
    args = SimpleNamespace(
        draft=draft["id"], injection_file="", watch_authorization=str(authorization)
    )

    assert calendar_cli.cmd_confirm(args) == 0
    assert executions == [draft["id"]]
    assert not authorization.exists()

    # Re-creating pending cannot make a pre-copied token with the same nonce usable.
    PendingConfirmStore().append(entry)
    monkeypatch.setattr(sys, "argv", [
        "calendar_cli", "confirm", "--draft", draft["id"],
        "--watch-authorization", str(copied_authorization),
    ])
    assert calendar_cli.main() != 0
    assert executions == [draft["id"]]


def test_direct_confirm_without_artifact_keeps_independent_discord_validation(
    tmp_path: Path, monkeypatch
) -> None:
    draft, _entry = _draft_with_pending(tmp_path, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "cha-owner")
    monkeypatch.setattr(calendar_confirm, "reject_cancel_reaction", lambda _draft: calls.append("cancel"))

    def missing_text(_draft):
        calls.append("text")
        raise calendar_gate.GateError("no text", 1)

    monkeypatch.setattr(calendar_confirm, "confirm_via_owner_scan", missing_text)
    monkeypatch.setattr(
        calendar_confirm, "confirm_via_reaction",
        lambda _draft: calls.append("reaction") or "reaction:msg-1",
    )
    monkeypatch.setattr(
        calendar_gate, "execute_draft",
        lambda current, _approval: calls.append("execute") or current["event_id"],
    )
    monkeypatch.setattr(
        calendar_cli.calendar_preflight,
        "read_event",
        lambda _calendar_id, event_id: {"id": event_id, "summary": draft["summary"]},
    )
    args = SimpleNamespace(draft=draft["id"], injection_file="", watch_authorization="")

    assert calendar_cli.cmd_confirm(args) == 0
    assert calls == ["cancel", "text", "reaction", "execute"]


def test_forged_or_cross_message_watcher_authorization_never_executes(
    tmp_path: Path, monkeypatch
) -> None:
    draft, entry = _draft_with_pending(tmp_path, monkeypatch)
    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "cha-owner")
    executions: list[str] = []
    monkeypatch.setattr(
        calendar_gate, "execute_draft",
        lambda current, _approval: executions.append(current["id"]) or current["event_id"],
    )

    forged = calendar_confirm.create_watcher_authorization(entry, "cha-owner")
    payload = json.loads(forged.read_text(encoding="utf-8"))
    payload["dm_message_id"] = "other-message"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    forged.chmod(0o600)
    forged_args = SimpleNamespace(
        draft=draft["id"], injection_file="", watch_authorization=str(forged)
    )
    try:
        calendar_cli.cmd_confirm(forged_args)
    except calendar_gate.GateError as error:
        assert error.exit_code == 1
    else:
        raise AssertionError("forged authorization was accepted")

    other_draft, other_entry = _draft_with_pending(tmp_path, monkeypatch, message_id="msg-2")
    cross = calendar_confirm.create_watcher_authorization(other_entry, "cha-owner")
    cross_args = SimpleNamespace(
        draft=draft["id"], injection_file="", watch_authorization=str(cross)
    )
    try:
        calendar_cli.cmd_confirm(cross_args)
    except calendar_gate.GateError as error:
        assert error.exit_code == 1
    else:
        raise AssertionError("cross-draft authorization was accepted")

    assert other_draft["id"] != draft["id"]
    assert executions == []
