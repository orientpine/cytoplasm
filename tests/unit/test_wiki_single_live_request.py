"""EXACTLY ONE live wiki confirm message per ``wiki:{action}:{slug}`` (façade migration).

The wiki gate used to rewrite ``confirm_message_id`` on every post, so the owner's ✅ on
the earlier message became permanently invisible. These specs pin the successor contract:
a stored id is never replaced — only superseded (delete BEFORE drop) or left alone.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "wiki" / "scripts"))

import wiki_gate  # noqa: E402

OWNER_ID = "owner-live-1"
CHANNEL_ID = "1526487935975952385"
SLUG = "single-live"


def _note(body: str) -> str:
    return (
        '---\ntitle: "Single Live"\ntags: [test]\ncreated: 2026-07-26T00:00:00Z\n'
        f"updated: 2026-07-26T00:00:00Z\nlinks: []\n---\n{body}\n"
    )


NOTE_A = _note("본문 A")
NOTE_B = _note("본문 B")
OWNER_REACTION: list[dict[str, str | bool]] = [{"id": OWNER_ID, "bot": False}]


@dataclass
class FakeDiscord:
    """Offline stand-in for ``wiki_gate._api`` — the seam every wiki gate test uses."""

    log: list[str] = field(default_factory=list)
    contents: dict[str, str] = field(default_factory=dict)
    channels: dict[str, str] = field(default_factory=dict)
    approve: dict[str, list[dict[str, str | bool]]] = field(default_factory=dict)
    cancel: dict[str, list[dict[str, str | bool]]] = field(default_factory=dict)
    deleted: set[str] = field(default_factory=set)
    posts: int = 0

    def __call__(
        self,
        method: str,
        path: str,
        payload: dict[str, str] | None = None,
    ) -> dict[str, str] | list[dict[str, str | bool]] | None:
        parts = path.strip("/").split("/")
        if method == "POST" and path == "/users/@me/channels":
            return {"id": CHANNEL_ID}
        if method == "GET" and path == f"/channels/{CHANNEL_ID}":
            return {"id": CHANNEL_ID, "name": "", "recipients": [{"id": OWNER_ID}], "type": 1}
        if method == "POST" and len(parts) == 3:
            self.posts += 1
            message_id = f"wiki-msg-{self.posts}"
            self.contents[message_id] = str((payload or {})["content"])
            self.channels[message_id] = parts[1]
            self.log.append(f"POST:{message_id}")
            return {"id": message_id, "channel_id": parts[1], "content": self.contents[message_id]}
        message_id = parts[3] if len(parts) > 3 else ""
        if method == "PUT":
            self.log.append(f"PUT:{message_id}")
            return None
        if method == "DELETE":
            self.log.append(f"DELETE:{message_id}")
            self.deleted.add(message_id)
            return None
        if method == "GET" and len(parts) > 5:
            emoji = unquote(parts[5].split("?", 1)[0])
            self.log.append(f"REACT:{message_id}:{emoji}")
            table = self.approve if emoji == wiki_gate.APPROVE_EMOJI else self.cancel
            return list(table.get(message_id, []))
        if method == "GET":
            self.log.append(f"GET:{message_id}")
            if message_id in self.deleted:
                raise HTTPError("https://discord.invalid", 404, "missing", None, None)
            return {
                "id": message_id,
                "channel_id": self.channels.get(message_id, parts[1]),
                "content": self.contents.get(message_id, ""),
            }
        raise AssertionError(f"unexpected Discord call: {method} {path}")


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDiscord:
    interop = tmp_path / "interop.json"
    interop.write_text(json.dumps({"owner_id": OWNER_ID}), encoding="utf-8")
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path / "wiki"))
    monkeypatch.setenv("WIKI_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-test-token")
    monkeypatch.setattr(wiki_gate, "GATE_DIR", tmp_path / "gate")
    monkeypatch.setattr(wiki_gate, "INTEROP_CONFIG", interop)
    transport = FakeDiscord()
    monkeypatch.setattr(wiki_gate, "_api", transport)
    write_json = wiki_gate._write_json

    def spy(path: Path, record: dict) -> None:
        transport.log.append(f"WRITE:{path.stem}")
        write_json(path, record)

    monkeypatch.setattr(wiki_gate, "_write_json", spy)
    return transport


def _draft(note_text: str) -> dict:
    return wiki_gate.create_draft("create", SLUG, note_text, CHANNEL_ID)


def _stored(tmp_path: Path, draft_id: str) -> dict:
    path = tmp_path / "gate" / "drafts" / f"{draft_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _live_ids(records: list[dict]) -> list[str]:
    return [str(record["confirm_message_id"]) for record in records if record.get("confirm_message_id")]


def test_second_request_with_the_same_hash_posts_nothing_and_keeps_the_id(
    fake: FakeDiscord,
    tmp_path: Path,
) -> None:
    # Given: one live confirm message the owner has not decided yet
    draft = _draft(NOTE_A)
    first = wiki_gate.post_confirm_message(draft)
    fake.log.clear()

    # When
    second = wiki_gate.post_confirm_message(wiki_gate.load_draft(draft["id"]))

    # Then
    assert first["confirm_message_id"] == "wiki-msg-1"
    assert second["confirm_message_id"] == "wiki-msg-1"
    assert _stored(tmp_path, draft["id"])["confirm_message_id"] == "wiki-msg-1"
    assert [entry for entry in fake.log if entry.startswith(("POST:", "DELETE:"))] == []
    assert _live_ids(wiki_gate.list_drafts()) == ["wiki-msg-1"]


def test_changed_content_deletes_the_old_message_before_dropping_the_record(
    fake: FakeDiscord,
    tmp_path: Path,
) -> None:
    # Given: a live message bound to the previous content of the same wiki:{action}:{slug}
    old = _draft(NOTE_A)
    wiki_gate.post_confirm_message(old)
    new = _draft(NOTE_B)
    fake.log.clear()

    # When
    posted = wiki_gate.post_confirm_message(new)

    # Then
    assert fake.log.index("DELETE:wiki-msg-1") < fake.log.index(f"WRITE:{old['id']}")
    assert fake.log.index(f"WRITE:{old['id']}") < fake.log.index("POST:wiki-msg-2")
    assert posted["confirm_message_id"] == "wiki-msg-2"
    assert "confirm_message_id" not in _stored(tmp_path, old["id"])
    assert _stored(tmp_path, new["id"])["confirm_message_id"] == "wiki-msg-2"
    assert _live_ids(wiki_gate.list_drafts()) == ["wiki-msg-2"]


def test_owner_already_decided_defers_without_deleting_the_live_message(
    fake: FakeDiscord,
    tmp_path: Path,
) -> None:
    # Given: the owner already reacted ✅ on the live message
    old = _draft(NOTE_A)
    wiki_gate.post_confirm_message(old)
    fake.approve["wiki-msg-1"] = OWNER_REACTION
    new = _draft(NOTE_B)
    fake.log.clear()

    # When / Then
    with pytest.raises(wiki_gate.GateError):
        wiki_gate.post_confirm_message(new)
    assert [entry for entry in fake.log if entry.startswith(("POST:", "DELETE:"))] == []
    assert _stored(tmp_path, old["id"])["confirm_message_id"] == "wiki-msg-1"
    assert "confirm_message_id" not in _stored(tmp_path, new["id"])


def test_corrupt_draft_file_refuses_the_request_without_posting(
    fake: FakeDiscord,
    tmp_path: Path,
) -> None:
    # Given: an unreadable draft record — a skipped draft would read as "nothing outstanding"
    old = _draft(NOTE_A)
    wiki_gate.post_confirm_message(old)
    (tmp_path / "gate" / "drafts" / "corrupt.json").write_text("{not json", encoding="utf-8")
    new = _draft(NOTE_B)
    fake.log.clear()

    # When / Then
    with pytest.raises(wiki_gate.GateError):
        wiki_gate.post_confirm_message(new)
    assert [entry for entry in fake.log if entry.startswith(("POST:", "DELETE:"))] == []
    assert _stored(tmp_path, old["id"])["confirm_message_id"] == "wiki-msg-1"


def test_binding_mismatch_refuses_without_deleting_or_dropping(
    fake: FakeDiscord,
    tmp_path: Path,
) -> None:
    # Given: the live message no longer carries the draft digest it was bound to
    old = _draft(NOTE_A)
    wiki_gate.post_confirm_message(old)
    fake.contents["wiki-msg-1"] = "저장 abc123 sha256:" + "f" * 64
    new = _draft(NOTE_B)
    fake.log.clear()

    # When / Then
    with pytest.raises(wiki_gate.GateError):
        wiki_gate.post_confirm_message(new)
    assert [entry for entry in fake.log if entry.startswith(("POST:", "DELETE:"))] == []
    assert _stored(tmp_path, old["id"])["confirm_message_id"] == "wiki-msg-1"
    assert _live_ids(wiki_gate.list_drafts()) == ["wiki-msg-1"]
