from __future__ import annotations

import importlib
import json
import sys
from argparse import Namespace
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError
from urllib.parse import unquote

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "calendar" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

calendar_cli = importlib.import_module("calendar_cli")
calendar_confirm = importlib.import_module("calendar_confirm")
calendar_gate = importlib.import_module("calendar_gate")
calendar_pending = importlib.import_module("calendar_pending")

OWNER = "owner-calendar"
KEY = "calendar:primary:event-1"
OWNER_DM_CHANNEL_ID = "1526487935975952385"


class OwnerDmDirectory:
    def owner_dm(self) -> str:
        return OWNER_DM_CHANNEL_ID

    def describe(self, channel_id: str):
        assert channel_id == OWNER_DM_CHANNEL_ID
        surface = importlib.import_module("automation.interop.approval_surface")
        return surface.ChannelFacts(1, "", (OWNER,))


class FakeDiscord:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.contents: dict[str, str] = {}
        self.approved: set[str] = set()
        self.deleted: set[str] = set()
        self.posts = 0

    def __call__(
        self, method: str, path: str, payload: dict[str, str] | None = None
    ) -> dict[str, str] | list[dict[str, str | bool]] | None:
        parts = path.strip("/").split("/")
        if method == "POST" and path == "/users/@me/channels":
            return {"id": OWNER_DM_CHANNEL_ID}
        if method == "GET" and path == f"/channels/{OWNER_DM_CHANNEL_ID}":
            return {"id": OWNER_DM_CHANNEL_ID, "name": "", "recipients": [{"id": OWNER}], "type": 1}
        if method == "POST" and len(parts) == 3:
            self.posts += 1
            message_id = f"msg-{self.posts}"
            self.contents[message_id] = str((payload or {})["content"])
            self.calls.append(f"POST:{message_id}")
            return {"id": message_id}
        message_id = parts[3] if len(parts) > 3 else ""
        if method == "PUT":
            return None
        if method == "DELETE":
            self.calls.append(f"DELETE:{message_id}")
            self.deleted.add(message_id)
            return None
        if method == "GET" and len(parts) > 5:
            emoji = unquote(parts[5].split("?", 1)[0])
            if emoji == calendar_confirm.APPROVE_EMOJI and message_id in self.approved:
                return [{"id": OWNER, "bot": False}]
            return []
        if method == "GET":
            if message_id in self.deleted:
                raise HTTPError("https://discord.invalid", 404, "missing", Message(), None)
            return {"id": message_id, "content": self.contents.get(message_id, "")}
        raise AssertionError(f"unexpected Discord call: {method} {path}")


@pytest.fixture
def calendar_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FakeDiscord, list[str]]:
    calls: list[str] = []
    fake = FakeDiscord(calls)
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("CALENDAR_PENDING_CONFIRMS", str(tmp_path / "pending.jsonl"))
    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: OWNER)
    monkeypatch.setattr(calendar_confirm, "_api", fake)
    return fake, calls


def _approval() -> ModuleType:
    return importlib.import_module("calendar_approval")


def _draft(*, summary: str = "same") -> dict[str, str | list[str]]:
    return calendar_gate.create_draft(
        action="update",
        argv=("gws", "calendar", "events", "patch", summary),
        calendar_id="primary",
        event_id="event-1",
        summary=summary,
        start="",
        end="",
        channel_id="dm",
    )


def _post(draft_id: str) -> int:
    return calendar_cli.cmd_post_confirm(Namespace(draft=draft_id))


def _store() -> calendar_pending.PendingConfirmStore:
    return calendar_pending.PendingConfirmStore()


def test_second_confirm_same_subject_and_hash_posts_nothing_and_keeps_message_id(
    calendar_env: tuple[FakeDiscord, list[str]],
) -> None:
    fake, _calls = calendar_env
    first, second = _draft(), _draft()
    assert first["sha256"] == second["sha256"]
    assert _post(first["id"]) == 0

    assert _post(second["id"]) == 0

    entries = tuple(entry for entry in _store().load() if entry.key == KEY)
    assert fake.posts == 1
    assert len(entries) == 1
    assert entries[0].dm_message_id == "msg-1"


def test_changed_content_deletes_message_before_dropping_row_then_posts_once(
    calendar_env: tuple[FakeDiscord, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, calls = calendar_env
    first, changed = _draft(summary="before"), _draft(summary="after")
    assert _post(first["id"]) == 0
    original_drop = getattr(calendar_pending.PendingConfirmStore, "drop", None)

    def recording_drop(store: calendar_pending.PendingConfirmStore, entry) -> None:
        calls.append(f"DROP:{entry.dm_message_id}")
        assert original_drop is not None
        original_drop(store, entry)

    monkeypatch.setattr(calendar_pending.PendingConfirmStore, "drop", recording_drop, raising=False)
    assert _post(changed["id"]) == 0

    assert calls.index("DELETE:msg-1") < calls.index("DROP:msg-1") < calls.index("POST:msg-2")
    entries = tuple(entry for entry in _store().load() if entry.key == KEY)
    assert fake.posts == 2
    assert [entry.dm_message_id for entry in entries] == ["msg-2"]


def test_owner_already_approved_defers_without_deleting_or_dropping(
    calendar_env: tuple[FakeDiscord, list[str]],
) -> None:
    fake, calls = calendar_env
    first, changed = _draft(summary="before"), _draft(summary="after")
    assert _post(first["id"]) == 0
    before = _store().path.read_bytes()
    fake.approved.add("msg-1")

    with pytest.raises(calendar_gate.GateError):
        _post(changed["id"])

    assert "DELETE:msg-1" not in calls
    assert fake.posts == 1
    assert _store().path.read_bytes() == before


def test_watcher_lease_causes_producer_to_change_nothing(
    calendar_env: tuple[FakeDiscord, list[str]],
) -> None:
    fake, _calls = calendar_env
    draft = _draft()
    approval = _approval()

    with approval.confirm_lease().hold(KEY) as owned:
        assert owned is True
        with pytest.raises(calendar_gate.GateError):
            _post(draft["id"])

    assert fake.posts == 0
    assert _store().load() == ()


def test_corrupt_row_refuses_without_posting(
    calendar_env: tuple[FakeDiscord, list[str]],
) -> None:
    fake, _calls = calendar_env
    draft = _draft()
    _store().path.parent.mkdir(parents=True, exist_ok=True)
    _store().path.write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(calendar_gate.GateError):
        _post(draft["id"])

    assert fake.posts == 0
    assert _store().path.read_text(encoding="utf-8") == "{not-json\n"


def test_calendar_legacy_record_derives_the_same_key_as_a_new_one(
    calendar_env: tuple[FakeDiscord, list[str]],
) -> None:
    _fake, _calls = calendar_env
    legacy, current = _draft(), _draft()
    store = _store()
    legacy_row = {
        "created": "2026-07-26T00:00:00Z",
        "dm_channel_id": "dm-1",
        "dm_message_id": "legacy-msg",
        "draft_id": legacy["id"],
        "sha256": legacy["sha256"],
    }
    current_entry = calendar_pending.PendingConfirm(
        draft_id=current["id"], sha256=current["sha256"], dm_channel_id="dm-1",
        dm_message_id="current-msg", created=datetime(2026, 7, 26, tzinfo=UTC), key=KEY,
    )
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(legacy_row) + "\n" + current_entry.as_json() + "\n", encoding="utf-8")

    requests = _approval().CalendarApprovalGate(current, store, OWNER).outstanding(KEY)

    assert [request.message_id for request in requests] == ["legacy-msg", "current-msg"]
    assert {request.key for request in requests} == {KEY}


def test_calendar_orphan_legacy_record_is_never_destroyed(
    calendar_env: tuple[FakeDiscord, list[str]],
) -> None:
    fake, _calls = calendar_env
    store = _store()
    orphan_id = "dead123"
    orphan_row = {
        "created": "2026-07-26T00:00:00Z", "dm_channel_id": "dm-1",
        "dm_message_id": "orphan-msg", "draft_id": orphan_id, "sha256": "old-hash",
    }
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(orphan_row) + "\n", encoding="utf-8")
    draft = _draft()

    assert _post(draft["id"]) == 0

    entries = store.load()
    assert {entry.key for entry in entries} == {f"calendar:__orphan__:{orphan_id}", KEY}
    assert [entry.dm_message_id for entry in entries] == ["orphan-msg", "msg-1"]
    assert fake.posts == 1


def test_legacy_dm_sentinel_record_still_resolves(
    calendar_env: tuple[FakeDiscord, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a deployed pre-binding record whose channel field carries the legacy sentinel.
    _fake, _calls = calendar_env
    binding = importlib.import_module("calendar_binding")
    monkeypatch.setattr(binding, "approval_directory", OwnerDmDirectory)
    entry = calendar_pending.PendingConfirm(
        draft_id="legacy", sha256="legacy-hash", dm_channel_id="dm",
        dm_message_id="legacy-message", created=datetime(2026, 7, 26, tzinfo=UTC),
    )

    # When: a lifecycle consumer reconstructs the request from that stored record.
    request = _approval().request_of(entry)

    # Then: the sentinel is resolved through legacy_binding and remains consumable.
    assert request.channel_id == OWNER_DM_CHANNEL_ID


def test_new_intent_carries_a_concrete_owner_dm_id(
    calendar_env: tuple[FakeDiscord, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the directory resolves the owner DM to a concrete snowflake.
    _fake, _calls = calendar_env
    binding = importlib.import_module("calendar_binding")
    monkeypatch.setattr(binding, "approval_directory", OwnerDmDirectory)
    draft = _draft()

    # When: the calendar flow creates a new lifecycle intent.
    intent = _approval().confirm_intent(draft)

    # Then: no new intent can carry the legacy "dm" sentinel.
    assert intent.channel_id == OWNER_DM_CHANNEL_ID
    assert intent.channel_id.isdigit()
