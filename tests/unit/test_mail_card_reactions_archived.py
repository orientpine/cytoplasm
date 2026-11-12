"""2026-11-04: a mail card in an archived thread must still get ✅⛔, and a card left without them
is given them by the watch tick - but only the one card the owner can still decide.

Draft 36b605 was adopted into a thread its withdrawn sibling had left 💤 and archived. Discord
refuses a reaction inside an archived thread (HTTP 400, code 50083), and adopt added the reactions
before the 🔔 rename that reopens the thread, so the card was bound with no buttons, only stderr
said so, and no later tick looked again. The thread is now reopened on its own (archived: false,
no name, so the two-renames-per-ten-minutes limit never touches it) before the reactions retry.
"""
from __future__ import annotations

import argparse
import io
import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from tests.unit.test_approval_thread_reuse import CARD, NAME, THREAD, _compose, _compose_api
from tests.unit.test_mail_compose_gate import triage_approval, triage_cli, triage_confirm, triage_gate

import triage_approval_gate

EMOJIS = ("✅", "⛔")


def _put(emoji: str) -> str:
    return f"/channels/{THREAD}/messages/{CARD}/reactions/{triage_confirm.quote(emoji, safe='')}/@me"


def _refused(status: int, code: int) -> HTTPError:
    body = io.BytesIO(json.dumps({"message": "refused", "code": code}).encode())
    return HTTPError("https://discord.invalid", status, "refused", Message(), body)


def _rate_limited() -> HTTPError:
    headers = Message()
    headers["Retry-After"] = "300"
    return HTTPError("https://discord.invalid", 429, "rate limited", headers, None)


class Discord:
    """The thread, the card and its reactions as Discord keeps them, over the compose fake."""

    def __init__(self, api, *, archived: bool) -> None:
        self.api = api
        self.archived = archived
        self.reactions: set[str] = set()
        self.content = ""
        self.calls: list[tuple[str, str, dict | None]] = []
        self.rename: object = {}
        self.refuse_reactions: HTTPError | None = None
        self.landed: list[int] = []

    def __call__(self, method: str, path: str, payload: dict | None = None) -> object:
        self.calls.append((method, path, payload))
        if method == "PUT" and "/reactions/" in path:
            if self.archived:
                raise _refused(400, 50083)
            if self.refuse_reactions is not None:
                raise self.refuse_reactions
            self.reactions.add(next(emoji for emoji in EMOJIS if path == _put(emoji)))
            self.landed.append(len(self.calls) - 1)
            return None
        if method == "PATCH" and path == f"/channels/{THREAD}":
            if payload == {"archived": False}:
                self.archived = False
                return {"id": THREAD}
            if isinstance(self.rename, Exception):
                raise self.rename
            self.archived = bool((payload or {}).get("archived", self.archived))
            return {"id": THREAD}
        if method == "GET" and path == f"/channels/{THREAD}":
            thread = self.api(method, path, payload)
            return {**thread, "thread_metadata": {**thread.get("thread_metadata", {}), "archived": self.archived}}
        if method == "GET" and path == f"/channels/{THREAD}/messages/{CARD}":
            return {"id": CARD, "author": {"bot": True}, "content": self.content,
                    "reactions": [{"emoji": {"name": emoji}, "me": True, "count": 1}
                                  for emoji in EMOJIS if emoji in self.reactions]}
        if method == "GET" and "/reactions/" in path:
            return []
        if method == "GET" and path.startswith(f"/channels/{THREAD}/messages?"):
            return [{"id": CARD, "author": {"bot": True}, "content": self.content, "reactions": []}]
        return self.api(method, path, payload)


def _posted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, str]:
    api = _compose_api(tmp_path, monkeypatch, "🔔 " + NAME)
    assert _compose() == 0
    posted, = triage_gate.list_drafts()
    content = next(call[2]["content"] for call in api.calls if call[:2] == ("POST", f"/channels/{THREAD}/messages"))
    return posted, content


def _half_posted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, Discord]:
    """The 36b605 state: card in an archived thread, draft unbound, journal reserved."""
    posted, content = _posted(tmp_path, monkeypatch)
    path = next((tmp_path / "gate").rglob(f"{posted['id']}.json"))
    stuck = {name: value for name, value in posted.items()
             if name not in ("message_id", "render_version", "card_sha256", "approval_created_at")}
    triage_gate.write_json(path, {**stuck, "message_id": ""})
    triage_approval.posting_journal().reserve(
        triage_approval.approval_key(posted), triage_approval._approval_action_hash(posted), "2026-11-04T09:48:58Z",
    )
    discord = Discord(triage_confirm._api, archived=True)
    discord.content = content
    monkeypatch.setattr(triage_confirm, "_api", discord)
    return posted, discord


def _bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, archived: bool) -> tuple[dict, Discord]:
    """A bound pending card whose ✅⛔ never landed (what 36b605 was after v2.7.25 adopted it)."""
    posted, content = _posted(tmp_path, monkeypatch)
    discord = Discord(triage_confirm._api, archived=archived)
    discord.content = content
    monkeypatch.setattr(triage_confirm, "_api", discord)
    return posted, discord


def _writes(discord: Discord) -> list[tuple[str, str, dict | None]]:
    return [call for call in discord.calls if call[0] in ("PUT", "PATCH")]


def test_adopt_reopens_an_archived_thread_before_adding_the_reactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the half-posted card sits in a thread its withdrawn sibling archived.
    posted, discord = _half_posted(tmp_path, monkeypatch)
    # When: the next tick reposts the unbound pending draft.
    assert triage_approval.post_for_approval(triage_gate.load_draft(posted["id"])) == CARD
    # Then: the thread is reopened alone (no name), both reactions land, and only then the 🔔 rename.
    assert discord.reactions == set(EMOJIS)
    reopen = discord.calls.index(("PATCH", f"/channels/{THREAD}", {"archived": False}))
    rename = next(index for index, (method, _, payload) in enumerate(discord.calls)
                  if method == "PATCH" and payload and "name" in payload)
    assert len(discord.landed) == 2 and reopen < min(discord.landed) and max(discord.landed) < rename


def test_a_rate_limited_rename_does_not_cost_the_adopted_card_its_reactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the archived thread's name was edited twice already (429 on the next rename).
    posted, discord = _half_posted(tmp_path, monkeypatch)
    discord.rename = _rate_limited()
    # When: the card is adopted.
    assert triage_approval.post_for_approval(triage_gate.load_draft(posted["id"])) == CARD
    # Then: the reopen is not a rename, so the reactions are there and the mark waits for a tick.
    assert discord.reactions == set(EMOJIS)
    root = tmp_path / "gate" / triage_approval_gate.THREAD_MARKS_DIRNAME
    assert [mark for _, (mark, _card) in triage_approval_gate._recorded_marks(root, THREAD)] == ["🔔"]
    err = capsys.readouterr().err
    assert "THREAD-RENAME-FAIL" in err and "APPROVAL-REACTION-FAIL" not in err


def test_compose_into_an_archived_thread_gets_both_reactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a thread that is archived when the reactions are added.
    api = _compose_api(tmp_path, monkeypatch, "💤 " + NAME)
    discord = Discord(api, archived=True)
    monkeypatch.setattr(triage_confirm, "_api", discord)
    # When: the real compose command posts the card.
    assert _compose() == 0
    # Then: the card carries both reactions.
    assert discord.reactions == set(EMOJIS)


def test_the_watch_tick_fills_missing_reactions_of_the_pending_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a bound pending card without ✅⛔ in an archived thread.
    posted, discord = _bound(tmp_path, monkeypatch, archived=True)
    # When: the watch tick looks at the drafts.
    assert triage_approval_gate.fill_missing_reactions(triage_gate.list_drafts()) == 0
    # Then: the thread is reopened alone and both reactions are added.
    assert discord.reactions == set(EMOJIS)
    assert ("PATCH", f"/channels/{THREAD}", {"archived": False}) in discord.calls
    assert f"APPROVAL-REACTIONS-FILLED draft={posted['id']} message={CARD}" in capsys.readouterr().out
    # And: a card that has them is left alone on the next tick.
    writes = len(_writes(discord))
    assert triage_approval_gate.fill_missing_reactions(triage_gate.list_drafts()) == 0
    assert len(_writes(discord)) == writes


@pytest.mark.parametrize("shape", ["expired", "withdrawn", "newer-card", "replaced-text"])
def test_the_watch_tick_never_fills_a_card_the_owner_must_not_decide(
    shape: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a card without ✅⛔ that is superseded (36b605), withdrawn, older than another card
    # in its thread, or whose message no longer holds the card bytes.
    posted, discord = _bound(tmp_path, monkeypatch, archived=False)
    drafts = triage_gate.list_drafts()
    if shape == "expired":
        drafts = [{**posted, "status": "expired", "expired_reason": "superseded-by-newer-draft"}]
    elif shape == "withdrawn":
        drafts = [{**posted, "status": "expired", "expired_reason": "withdrawn-by-requester"}]
    elif shape == "newer-card":
        drafts = [*drafts, {**posted, "id": "f00f00", "status": "sent", "message_id": str(int(CARD) + 1)}]
    else:
        discord.content = "🔁 새 판으로 대체됨 — 이 카드는 실행되지 않습니다. 아래 새 카드에서 승인해 주세요."
    # When: the watch tick looks at them.
    assert triage_approval_gate.fill_missing_reactions(drafts) == 0
    # Then: nothing is written - no reopen, no reaction.
    assert _writes(discord) == []
    assert discord.reactions == set()


def test_a_reaction_that_still_fails_fails_the_watch_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a bound pending card whose reactions Discord keeps refusing.
    posted, discord = _bound(tmp_path, monkeypatch, archived=False)
    discord.refuse_reactions = _refused(403, 50013)
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.setattr(triage_cli, "_digest_reactions", lambda: None)
    monkeypatch.setattr(triage_cli, "_remind_pending", lambda *_a: None)
    monkeypatch.setattr(triage_confirm, "resolve_reaction", lambda _d: None)
    monkeypatch.setattr(triage_cli.triage_approval, "expire_retired_approval", lambda _d: False)
    import triage_digest_thread

    monkeypatch.setattr(triage_digest_thread, "settle", lambda: None)
    # When: the real watch tick runs.
    rc = triage_cli.cmd_watch(argparse.Namespace())
    # Then: the tick fails and says which card has no buttons.
    assert rc == 1
    err = capsys.readouterr().err
    assert f"APPROVAL-REACTION-FAIL ✅ message={CARD}" in err
    assert f"APPROVAL-REACTIONS-MISSING draft={posted['id']} message={CARD}" in err
