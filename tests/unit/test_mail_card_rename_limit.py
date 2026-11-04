"""2026-11-04: a thread rename rate limit (429) must never cost a posted mail card its binding.

Discord allows two channel-name edits per ten minutes. Withdrawing a card marked its thread 💤 and
the revised card's 🔔 rename then hit 429; the shared REST helper slept on Retry-After, the node gate
killed compose at 150 s, and the card was left without reactions, without its message id and with a
posting journal no later tick could resolve (refused:posting-journal-stale).
"""
from __future__ import annotations

from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from tests.unit.test_approval_thread_reuse import CARD, NAME, THREAD, _compose, _compose_api
from tests.unit.test_mail_compose_gate import triage_approval, triage_confirm, triage_gate

import triage_approval_gate


def _rate_limited() -> HTTPError:
    headers = Message()
    headers["Retry-After"] = "300"
    return HTTPError("https://discord.test", 429, "rate limited", headers, None)


def _marks(tmp_path: Path) -> list[str]:
    root = tmp_path / "gate" / triage_approval_gate.THREAD_MARKS_DIRNAME
    return sorted(path.name for path in root.glob("*")) if root.is_dir() else []


def test_rate_limited_rename_keeps_the_card_bound_with_reactions_and_remembers_the_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the reused thread shows 💤 and Discord refuses its rename with 429.
    api = _compose_api(tmp_path, monkeypatch, "💤 " + NAME)
    api.responses[("PATCH", f"/channels/{THREAD}")] = _rate_limited()
    # When: the real compose command posts the revised card.
    assert _compose() == 0
    # Then: the card is bound, both reactions came before the one rename attempt, and the
    # thread is remembered for a later tick.
    draft, = triage_gate.list_drafts()
    assert draft["message_id"] == CARD and draft["status"] == "pending"
    methods = [call[0] for call in api.calls if call[0] in ("PUT", "PATCH")]
    assert methods == ["PUT", "PUT", "PATCH"]
    assert triage_approval.posting_journal().outstanding(triage_approval.approval_key(draft)) is None
    assert _marks(tmp_path) == [THREAD]
    assert "THREAD-RENAME-FAIL" in capsys.readouterr().err


def test_card_is_bound_before_anything_after_the_post_can_hang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the process dies right after the card POST (the gate's 150 s kill).
    api = _compose_api(tmp_path, monkeypatch, "💤 " + NAME)
    put = f"/channels/{THREAD}/messages/{CARD}/reactions/{triage_confirm.quote('✅', safe='')}/@me"
    api.responses[("PUT", put)] = KeyboardInterrupt()
    # When: compose is interrupted while adding the first reaction.
    with pytest.raises(KeyboardInterrupt):
        _compose()
    # Then: the draft already holds the posted card and no stale journal is left behind.
    draft, = triage_gate.list_drafts()
    assert draft["message_id"] == CARD and draft["channel_id"] == THREAD
    assert triage_approval.posting_journal().outstanding(triage_approval.approval_key(draft)) is None


def test_api_does_not_sleep_on_429_inside_no_wait() -> None:
    sleeps: list[float] = []
    calls: list[int] = []

    def send(_request: object) -> object:
        calls.append(1)
        raise _rate_limited()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(triage_confirm, "bot_token", lambda: "identity-a")
        patch.setattr(triage_confirm, "_send", send)
        patch.setattr(triage_confirm.time, "sleep", sleeps.append)
        with triage_confirm.no_rate_limit_wait(), pytest.raises(HTTPError):
            triage_confirm._api("PATCH", f"/channels/{THREAD}", {"name": "x"})
        assert (sleeps, len(calls)) == ([], 1)
        with pytest.raises(HTTPError):
            triage_confirm._api("GET", f"/channels/{THREAD}")
        assert sleeps == [300.0] * (triage_confirm._RATE_LIMIT_ATTEMPTS - 1)


def test_watch_tick_sets_the_pending_mark_once_the_limit_lifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: compose left a remembered thread because its rename was rate limited.
    api = _compose_api(tmp_path, monkeypatch, "💤 " + NAME)
    api.responses[("PATCH", f"/channels/{THREAD}")] = _rate_limited()
    assert _compose() == 0
    # When: a later tick runs after the limit lifted.
    api.responses[("PATCH", f"/channels/{THREAD}")] = {}
    triage_approval_gate.retry_thread_marks(triage_gate.list_drafts())
    # Then: the thread gets 🔔 and the reminder is gone.
    assert api.calls[-1] == ("PATCH", f"/channels/{THREAD}", {"name": "🔔 ✉️ request", "archived": False})
    assert _marks(tmp_path) == []


def test_watch_tick_drops_a_mark_whose_card_is_no_longer_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _compose_api(tmp_path, monkeypatch, "💤 " + NAME)
    api.responses[("PATCH", f"/channels/{THREAD}")] = _rate_limited()
    assert _compose() == 0
    draft, = triage_gate.list_drafts()
    calls = len(api.calls)
    # When: the owner decided the card before the tick (its close set the end mark).
    triage_approval_gate.retry_thread_marks([{**draft, "status": "sent"}])
    # Then: the tick never renames the decided thread back and forgets it.
    assert len(api.calls) == calls
    assert _marks(tmp_path) == []


def test_a_half_posted_card_is_adopted_instead_of_wedging_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the 2026-11-04 state - the card is in the thread, the draft has no message id,
    # and the posting journal holds a reservation without one.
    api = _compose_api(tmp_path, monkeypatch, "🔔 " + NAME)
    assert _compose() == 0
    posted, = triage_gate.list_drafts()
    content = next(call[2]["content"] for call in api.calls if call[:2] == ("POST", f"/channels/{THREAD}/messages"))
    key = triage_approval.approval_key(posted)
    path = tmp_path / "gate" / "drafts" / f"{posted['id']}.json"
    if not path.exists():
        path = next((tmp_path / "gate").rglob(f"{posted['id']}.json"))
    stuck = {name: value for name, value in posted.items()
             if name not in ("message_id", "render_version", "card_sha256", "approval_created_at")}
    triage_gate.write_json(path, {**stuck, "message_id": ""})
    journal = triage_approval.posting_journal()
    journal.reserve(key, triage_approval._approval_action_hash(posted), "2026-11-04T09:48:58Z")
    seen: list[tuple[str, str]] = []

    def discord(method: str, path: str, payload: dict | None = None) -> object:
        seen.append((method, path))
        if method == "GET" and path.startswith(f"/channels/{THREAD}/messages?"):
            return [{"id": "999", "author": {"bot": True}, "content": "other", "reactions": []},
                    {"id": CARD, "author": {"bot": True}, "content": content, "reactions": []}]
        if method == "GET" and path == f"/channels/{THREAD}/messages/{CARD}":
            return {"id": CARD, "content": content}
        if method == "GET" and "/reactions/" in path:
            return []
        return api(method, path, payload)

    monkeypatch.setattr(triage_confirm, "_api", discord)
    # When: the next watch tick reposts the unbound pending draft.
    assert triage_approval.post_for_approval(triage_gate.load_draft(posted["id"])) == CARD
    # Then: the existing card is bound with both reactions; no second card is posted.
    draft, = triage_gate.list_drafts()
    assert draft["message_id"] == CARD and draft["card_sha256"] == posted["card_sha256"]
    assert journal.outstanding(key) is None
    assert ("POST", f"/channels/{THREAD}/messages") not in seen
    assert [path for method, path in seen if method == "PUT"] == [
        f"/channels/{THREAD}/messages/{CARD}/reactions/{triage_confirm.quote(emoji, safe='')}/@me"
        for emoji in ("✅", "⛔")
    ]
