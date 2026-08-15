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
_COORDINATION = _REPO / "skills" / "coordination" / "scripts"
_CALENDAR = _REPO / "skills" / "calendar" / "scripts"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_CALENDAR))
sys.path.insert(0, str(_COORDINATION))

io = importlib.import_module("coordinate_io")
coordination_lifecycle = importlib.import_module("coordination_lifecycle")
coordination_pending = importlib.import_module("coordination_pending")

OWNER = "owner-coordination"
SLOT = "2026-07-28T09:00:00+09:00"
KEY = f"coord:{SLOT}"
OWNER_DM_CHANNEL_ID = "1526487935975952385"


class FakeDiscord:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.contents: dict[str, str] = {}
        self.approved: set[str] = set()
        self.deleted: set[str] = set()
        self.posts = 0

    def owner_channel(self, _owner_id: str) -> str:
        return "dm-1"

    def post(self, _channel_id: str, content: str) -> str:
        self.posts += 1
        message_id = f"msg-{self.posts}"
        self.contents[message_id] = content
        self.calls.append(f"POST:{message_id}")
        return message_id

    def add_reaction(self, _channel_id: str, _message_id: str, _emoji: str) -> None:
        return None

    def api(
        self, method: str, path: str, _payload: dict[str, str] | None = None
    ) -> dict[str, str] | list[dict[str, str | bool]] | None:
        parts = path.strip("/").split("/")
        if method == "POST" and path == "/users/@me/channels":
            return {"id": OWNER_DM_CHANNEL_ID}
        if method == "GET" and path == f"/channels/{OWNER_DM_CHANNEL_ID}":
            return {"id": OWNER_DM_CHANNEL_ID, "name": "", "recipients": [{"id": OWNER}], "type": 1}
        message_id = parts[3] if len(parts) > 3 else ""
        if method == "DELETE":
            self.calls.append(f"DELETE:{message_id}")
            self.deleted.add(message_id)
            return None
        if method == "GET" and len(parts) > 5:
            emoji = unquote(parts[5].split("?", 1)[0])
            if emoji == "✅" and message_id in self.approved:
                return [{"id": OWNER, "bot": False}]
            return []
        if method == "GET":
            if message_id in self.deleted:
                raise HTTPError("https://discord.invalid", 404, "missing", Message(), None)
            return {"id": message_id, "content": self.contents.get(message_id, "")}
        raise AssertionError(f"unexpected Discord call: {method} {path}")


@pytest.fixture
def coordination_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[FakeDiscord, list[str]]:
    calls: list[str] = []
    fake = FakeDiscord(calls)
    interop = tmp_path / "interop.json"
    interop.write_text(json.dumps({"agent_id": "agent", "owner_id": OWNER}), encoding="utf-8")
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "calendar-gate"))
    monkeypatch.setenv("COORDINATION_PENDING_CONFIRMS", str(tmp_path / "pending.jsonl"))
    monkeypatch.setenv("COORDINATION_STATE_DIR", str(tmp_path / "coordination"))
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    monkeypatch.setattr(io, "post_message", fake.post)
    monkeypatch.setattr(io, "add_reaction", fake.add_reaction)
    monkeypatch.setattr(io, "api", fake.api)
    monkeypatch.setattr(io, "obs", lambda **_fields: None)
    return fake, calls


def _approval() -> ModuleType:
    return importlib.import_module("coordination_approval")


def _args(summary: str = "peer meeting") -> Namespace:
    return Namespace(
        summary=summary,
        duration_min=30,
        calendar="primary",
        e2e_confirm=False,
        peer="agent-peer",
    )


def _request(summary: str = "peer meeting", correlation: str = "coord-run") -> int:
    return coordination_lifecycle.owner_leg(
        _args(summary), {"owner_id": OWNER}, correlation, None, SLOT
    )


def _store() -> coordination_pending.PendingConfirmStore:
    return coordination_pending.PendingConfirmStore()


def test_second_confirm_same_slot_and_hash_posts_nothing_and_keeps_message_id(
    coordination_env: tuple[FakeDiscord, list[str]],
) -> None:
    fake, _calls = coordination_env
    assert _request(correlation="coord-first") == 7

    assert _request(correlation="coord-second") == 7

    entries = tuple(entry for entry in _store().load() if entry.key == KEY)
    assert fake.posts == 1
    assert len(entries) == 1
    assert entries[0].dm_message_id == "msg-1"


def test_changed_content_deletes_message_before_dropping_row_then_posts_once(
    coordination_env: tuple[FakeDiscord, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, calls = coordination_env
    assert _request("before") == 7
    original_drop = getattr(coordination_pending.PendingConfirmStore, "drop", None)

    def recording_drop(store: coordination_pending.PendingConfirmStore, entry) -> None:
        calls.append(f"DROP:{entry.dm_message_id}")
        assert original_drop is not None
        original_drop(store, entry)

    monkeypatch.setattr(coordination_pending.PendingConfirmStore, "drop", recording_drop, raising=False)
    assert _request("after") == 7

    assert calls.index("DELETE:msg-1") < calls.index("DROP:msg-1") < calls.index("POST:msg-2")
    entries = tuple(entry for entry in _store().load() if entry.key == KEY)
    assert fake.posts == 2
    assert [entry.dm_message_id for entry in entries] == ["msg-2"]


def test_owner_already_approved_defers_without_deleting_or_dropping(
    coordination_env: tuple[FakeDiscord, list[str]],
) -> None:
    fake, calls = coordination_env
    assert _request("before") == 7
    before = _store().path.read_bytes()
    fake.approved.add("msg-1")

    with pytest.raises(io.CoordinationError):
        _request("after")

    assert "DELETE:msg-1" not in calls
    assert fake.posts == 1
    assert _store().path.read_bytes() == before


def test_watcher_lease_causes_producer_to_change_nothing(
    coordination_env: tuple[FakeDiscord, list[str]],
) -> None:
    fake, _calls = coordination_env
    approval = _approval()

    with approval.confirm_lease().hold(KEY) as owned:
        assert owned is True
        with pytest.raises(io.CoordinationError):
            _request()

    assert fake.posts == 0
    assert _store().load() == ()


def test_corrupt_row_refuses_without_posting(
    coordination_env: tuple[FakeDiscord, list[str]],
) -> None:
    fake, _calls = coordination_env
    _store().path.parent.mkdir(parents=True, exist_ok=True)
    _store().path.write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(io.CoordinationError):
        _request()

    assert fake.posts == 0
    assert _store().path.read_text(encoding="utf-8") == "{not-json\n"


def test_coordination_legacy_record_derives_key_from_slot(
    coordination_env: tuple[FakeDiscord, list[str]],
) -> None:
    _fake, _calls = coordination_env
    store = _store()
    base = {
        "correlation": "coord-legacy", "created": "2026-07-26T00:00:00Z",
        "dm_channel_id": "dm-1", "draft_id": "abc123", "duration_min": 30,
        "sha256": "sha-123", "slot": SLOT, "summary": "peer meeting",
    }
    legacy = {**base, "dm_message_id": "legacy-msg"}
    current = coordination_pending.PendingConfirm(
        draft_id="def456", sha256="sha-456", dm_channel_id="dm-1",
        dm_message_id="current-msg", slot=SLOT, summary="peer meeting",
        correlation="coord-current", duration_min=30,
        created=datetime(2026, 7, 26, tzinfo=UTC), key=KEY,
    )
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(legacy) + "\n" + current.as_json() + "\n", encoding="utf-8")

    requests = _approval().CoordinationApprovalGate(None, store, OWNER).outstanding(KEY)

    assert [request.message_id for request in requests] == ["legacy-msg", "current-msg"]
    assert {request.key for request in requests} == {KEY}
