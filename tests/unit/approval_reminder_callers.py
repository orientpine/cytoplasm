"""Injected caller runtimes; keep coordinate regressions out of oversized watcher suites."""
from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, tzinfo
from importlib import import_module
from pathlib import Path
from typing import Final, Protocol, override

import pytest

from automation.interop.approval_lease import FileKeyLease
from automation.interop.approval_reminder_config import ApprovalReminderConfig
from automation.interop.approval_surface import ChannelFacts, POLICY_VERSION

ROOT: Final = Path(__file__).resolve().parents[2]
POSTED: Final = datetime(2026, 9, 1, tzinfo=UTC)
NOW: Final = POSTED + timedelta(hours=3)


class Clock(datetime):
    @classmethod
    @override
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return NOW.astimezone(tz)


@dataclass(frozen=True, slots=True)
class Case:
    guild_id: str | int | None = None
    dm: bool = False
    enabled: bool = True

    @property
    def surface(self) -> str:
        return "owner-dm" if self.dm else "agent-chat-thread"

    @property
    def policy(self) -> int:
        return 0 if self.dm else POLICY_VERSION


@dataclass(frozen=True, slots=True)
class Directory:
    dm: bool = False

    def agent_chat(self) -> str:
        return "444"

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == "222"
        return (ChannelFacts(1, "", ("555",)) if self.dm
                else ChannelFacts(11, "request", (), "444", "999"))


class CalendarEntry(Protocol):
    @property
    def dm_channel_id(self) -> str: ...

    @property
    def dm_message_id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class Delivery:
    """Accumulate only observable transport posts; pending probes are immutable."""
    posts: list[tuple[str, str]] = field(default_factory=list)

    def post_message(self, channel_id: str, content: str) -> str:
        self.posts.append((channel_id, content))
        return "666"

    def get_message(self, channel_id: str, message_id: str) -> str:
        assert (channel_id, message_id) == ("222", "333")
        return "sha256:bound"

    def get_reaction_users(self, channel_id: str, message_id: str, emoji: str) -> tuple[()]:
        assert (channel_id, message_id) == ("222", "333")
        assert emoji in ("✅", "⛔")
        return ()

    def fetch_channel(self, channel_id: str) -> dict[str, str]:
        assert channel_id == "222"
        return {"id": channel_id}

    def message_content(self, entry: CalendarEntry) -> str:
        return self.get_message(entry.dm_channel_id, entry.dm_message_id)

    def reaction_users(self, entry: CalendarEntry, emoji: str) -> tuple[()]:
        return self.get_reaction_users(entry.dm_channel_id, entry.dm_message_id, emoji)

    def send_owner_dm(self, content: str) -> None:
        self.posts.append(("owner-callback", content))

    def confirm(self, entry: CalendarEntry, owner_id: str) -> None:
        pytest.fail("pending reminder must not execute")

    def discard(self, draft_id: str) -> None:
        pytest.fail("pending reminder must not discard")


@dataclass(frozen=True, slots=True)
class Runtime:
    tick: Callable[[], None]
    delivery: Delivery


@dataclass(frozen=True, slots=True)
class Setup:
    root: Path
    patch: pytest.MonkeyPatch
    case: Case

    def mail(self) -> Runtime:
        self.patch.syspath_prepend(str(ROOT / "skills/mail/scripts"))
        cli = import_module("triage_cli")
        binding = import_module("triage_binding")
        confirm = import_module("triage_confirm")
        delivery = Delivery()
        draft: dict[str, str | int] = {
            "id": "draft", "uid": "fixture", "kind": "compose", "sha256": "sha256:bound",
            "message_id": "333", "channel_id": "222", "approval_thread_id": "222",
            "surface": self.case.surface, "policy_version": self.case.policy,
            "created": POSTED.isoformat(),
        }
        if self.case.guild_id is not None:
            draft["approval_guild_id"] = self.case.guild_id
        self.patch.setenv("TRIAGE_GATE_DIR", str(self.root / "mail"))
        self.patch.setattr(cli, "datetime", Clock)
        self.patch.setattr(binding, "approval_directory", lambda: Directory(self.case.dm))
        self.patch.setattr(confirm, "owner_id", lambda: "555")

        def api(method: str, path: str, payload: dict[str, str] | None = None) -> dict[str, str] | list[dict[str, str]]:
            if method == "POST":
                assert path == "/channels/222/messages" and payload is not None
                return {"id": delivery.post_message("222", payload["content"])}
            assert method == "GET"
            if path == "/channels/222":
                return delivery.fetch_channel("222")
            if "/reactions/" in path:
                return []
            assert path == "/channels/222/messages/333"
            return {"content": "sha256:bound"}

        self.patch.setattr(confirm, "_api", api)
        return Runtime(lambda: cli._remind_pending(
            draft, ApprovalReminderConfig(enabled=self.case.enabled)), delivery)

    def calendar(self) -> Runtime:
        scripts = ROOT / "skills/calendar/scripts"
        self.patch.syspath_prepend(str(scripts))
        self.patch.setenv("CALENDAR_SCRIPTS", str(scripts))
        self.patch.setenv("CALENDAR_GATE_DIR", str(self.root / "calendar"))
        spec = importlib.util.spec_from_file_location("t36_calendar_watch", scripts / "confirm_reaction_watch.py")
        assert spec is not None and spec.loader is not None
        watch = importlib.util.module_from_spec(spec)
        self.patch.setitem(sys.modules, spec.name, watch)
        spec.loader.exec_module(watch)
        binding = import_module("calendar_binding")
        self.patch.setattr(binding, "approval_directory", lambda: Directory(self.case.dm))
        self.patch.setattr(watch.calendar_confirm, "owner_id", lambda: "555")
        entry = watch.PendingConfirm("draft", "bound", "222", "333", POSTED,
                                     kind="calendar", surface=self.case.surface,
                                     channel_id="222", policy_version=self.case.policy)
        store = watch.PendingConfirmStore(self.root / "calendar/pending.jsonl")
        store.append(entry)
        draft: dict[str, str | int] = {"id": "draft", "approval_thread_id": "222"}
        if self.case.guild_id is not None:
            draft["approval_guild_id"] = self.case.guild_id
        delivery = Delivery()
        return Runtime(lambda: watch.run_once(
            store=store, owner_id="555", discord=delivery, commands=delivery,
            draft_sha256=lambda _draft: "bound", draft_record=lambda _draft: draft,
            now=NOW, reminder_config=ApprovalReminderConfig(enabled=self.case.enabled)), delivery)

    def todo(self) -> Runtime:
        self.patch.syspath_prepend(str(ROOT / "skills/todo/scripts"))
        self.patch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(ROOT))
        model = import_module("todo_approval_model")
        stores = import_module("todo_approval_store")
        watch = import_module("todo_confirm_reaction_watch")
        store = stores.TodoApprovalStore(self.root / "todo")
        spec = model.TodoApprovalSpec(
            "todo:fixture", "sha256:bound", "fixture", "fixture", "todo",
            self.case.surface, "222", self.case.policy, approval_thread_id="222",
            approval_guild_id=None if isinstance(self.case.guild_id, int) else self.case.guild_id,
        )
        bound = store.bind_message(store.prepare(spec, POSTED), "333")
        if isinstance(self.case.guild_id, int):
            # Inject malformed optional metadata after the strict JSON parser, not into approval state.
            record = replace(bound, approval_guild_id=self.case.guild_id)
            self.patch.setattr(stores.TodoApprovalStore, "all_outstanding", lambda _store: (record,))
        delivery = Delivery()
        return Runtime(lambda: watch.run_once(
            store=store, owner_id="555", transport=delivery, directory=Directory(self.case.dm),
            approval_log=self.root / "approvals.jsonl", lease=FileKeyLease(self.root / "leases"),
            now=NOW, reminder_config=ApprovalReminderConfig(enabled=self.case.enabled)), delivery)
