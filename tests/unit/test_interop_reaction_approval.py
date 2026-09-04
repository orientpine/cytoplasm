"""The shared reaction transport, ✅ transcription and request-thread reuse.

plaud_sync and memory_relocate each carried a byte-identical copy of this code;
the copy existed only because importing ``memory_relocate.effects_live`` drags the
memory_curator chain into the plaud watcher. These tests pin the one property that
made the duplication look unavoidable — the shared module pulls in NEITHER watcher —
so the next reader cannot "fix" an import and quietly resurrect the fork.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import Final

import pytest

from automation.interop.reaction_approval import (
    DiscordTransport,
    DiscordTransportError,
    record_push_approval,
    thread_candidates,
)

_REPO: Final = Path(__file__).resolve().parents[2]
_MODULE: Final = "automation.interop.reaction_approval"


def test_module_when_imported_then_drags_in_no_watcher_package() -> None:
    # Given: a clean interpreter that imports ONLY the shared module.
    probe = (
        "import sys;"
        f"__import__('{_MODULE}');"
        "print('\\n'.join(sorted(n for n in sys.modules"
        " if n.split('.')[0] == 'automation')))"
    )

    # When: the import graph is observed from that fresh process.
    result = subprocess.run(  # noqa: S603 - fixed interpreter, literal argument
        [sys.executable, "-c", probe],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    loaded = set(result.stdout.split())

    # Then: none of the heavy watcher chains came with it — that is the whole point
    # of hosting the transport in interop instead of forking it per watcher.
    forbidden = {
        name
        for name in loaded
        if name.startswith(
            ("automation.memory_curator", "automation.memory_relocate", "automation.plaud_sync")
        )
    }
    assert not forbidden, f"shared module imports a watcher package: {sorted(forbidden)}"


def test_record_push_approval_writes_the_record_the_effect_gate_accepts(tmp_path: Path) -> None:
    # Given: an approval log the external-effect gate reads.
    log = tmp_path / "state" / "push-approvals.jsonl"

    # When: the owner's ✅ on the bound message is transcribed.
    record_push_approval(
        log,
        action_hash="sha256:" + "a" * 64,
        target_id="000_PARA/Area/note.md",
        owner_id="owner-1",
        message_id="msg-1",
        now=datetime(2026, 9, 2, 3, 4, 5, tzinfo=UTC),
    )

    # Then: the gate's schema is written verbatim, bound to that exact message.
    payload = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert payload == {
        "action": "external_effect.approval",
        "approval": {
            "channel": "approvals",
            "message_id": "msg-1",
            "method": "manual_reaction",
            "owner_id": "owner-1",
        },
        "hash": "sha256:" + "a" * 64,
        "result": {"status": "approved"},
        "target_id": "000_PARA/Area/note.md",
        "timestamp": "2026-09-02T03:04:05Z",
    }
    assert log.stat().st_mode & 0o777 == 0o600


def test_record_push_approval_appends_without_clobbering_earlier_decisions(
    tmp_path: Path,
) -> None:
    # Given: a log that already holds one transcribed approval.
    log = tmp_path / "push-approvals.jsonl"
    for message_id in ("msg-1", "msg-2"):
        # When: a second decision is transcribed.
        record_push_approval(
            log,
            action_hash="sha256:" + "b" * 64,
            target_id="t",
            owner_id="owner-1",
            message_id=message_id,
        )

    # Then: both survive — a lost approval means a silently refused push.
    lines = log.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["approval"]["message_id"] for line in lines] == ["msg-1", "msg-2"]


@dataclass(frozen=True, slots=True)
class _Card:
    """The only field the thread-reuse helper is allowed to know about."""

    key: str
    channel_id: str


def _rebind(card: _Card):
    return lambda thread_id: replace(card, channel_id=thread_id)


def test_thread_candidates_prefers_the_live_request_of_the_same_key() -> None:
    # Given: a live request whose approval message is still posted.
    card = _Card("rec-1", "chan-1")
    live = (replace(card, channel_id="thread-live"),)

    # When/Then: the live request lends its thread untouched.
    assert thread_candidates(live, approval_thread_id="thread-old", rebind=_rebind(card)) == live


def test_thread_candidates_falls_back_to_the_thread_the_request_already_opened() -> None:
    # Given: no live request (the approval message went missing) but a remembered thread.
    card = _Card("rec-1", "chan-1")

    # When: candidates are resolved for the re-post.
    (candidate,) = thread_candidates(
        (), approval_thread_id="thread-7", rebind=_rebind(card)
    )

    # Then: the re-post is steered back into that thread — resolving a fresh binding
    # here would open a SECOND thread for one request.
    assert candidate == _Card("rec-1", "thread-7")


def test_thread_candidates_is_empty_when_no_thread_was_ever_bound() -> None:
    # Given: a request that never reached a surface.
    card = _Card("rec-1", "")

    # When/Then: nothing to reuse — the caller resolves a new binding as before.
    assert thread_candidates((), approval_thread_id=None, rebind=_rebind(card)) == ()
    assert thread_candidates((), approval_thread_id="", rebind=_rebind(card)) == ()


@dataclass(frozen=True, slots=True)
class _Response:
    status: int
    reason: str
    headers: Message
    body: bytes

    def read(self) -> bytes:
        return self.body


class _Connection:
    def __init__(self, responses: list[_Response], requests: list[tuple[str, str]]) -> None:
        self._responses = responses
        self._requests = requests

    def request(self, method: str, path: str, body: bytes | None = None, headers=None) -> None:
        del body, headers
        self._requests.append((method, path))

    def getresponse(self) -> _Response:
        return self._responses.pop(0)

    def close(self) -> None:
        return None


def _install(
    monkeypatch: pytest.MonkeyPatch, responses: list[_Response]
) -> list[tuple[str, str]]:
    requests: list[tuple[str, str]] = []
    monkeypatch.setattr(
        f"{_MODULE}.HTTPSConnection",
        lambda host, timeout: _Connection(responses, requests),
    )
    return requests


def _ok(body: bytes) -> _Response:
    return _Response(200, "OK", Message(), body)


def _rate_limited(retry_after: str) -> _Response:
    headers = Message()
    headers["Retry-After"] = retry_after
    return _Response(429, "rate limited", headers, b"")


def test_transport_reads_reaction_users_with_their_bot_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Discord lists one human and the bot's own seeded reaction.
    requests = _install(
        monkeypatch, [_ok(b'[{"id": "9", "bot": false}, {"id": "bot", "bot": true}]')]
    )
    transport = DiscordTransport("t", "9")

    # When: the gate reads who reacted.
    users = transport.get_reaction_users("chan-1", "msg-1", "✅")

    # Then: the bot flag survives (the gate must not read its own seed as approval)
    # and the emoji is percent-encoded into the path.
    assert users == (("9", False), ("bot", True))
    assert requests == [("GET", "/api/v10/channels/chan-1/messages/msg-1/reactions/%E2%9C%85")]


def test_transport_reports_a_deleted_message_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the approval card was deleted by hand.
    _install(monkeypatch, [_Response(404, "Not Found", Message(), b"")])
    transport = DiscordTransport("t", "9")

    # When/Then: a 404 is the MISSING signal, not an error the tick dies on.
    assert transport.get_message("chan-1", "msg-1") is None


def test_transport_honors_retry_after_then_repeats_the_same_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one rate-limit answer carrying an explicit delay.
    requests = _install(monkeypatch, [_rate_limited("2.5"), _ok(b'{"id": "msg-9"}')])
    sleeps: list[float] = []
    transport = DiscordTransport("t", "9", sleeper=sleeps.append, max_attempts=3)

    # When: a message is posted through the rate limit.
    message_id = transport.post_message("chan-1", "hello")

    # Then: it waits exactly as told and re-issues the identical request.
    assert message_id == "msg-9"
    assert sleeps == [2.5]
    assert requests == [
        ("POST", "/api/v10/channels/chan-1/messages"),
        ("POST", "/api/v10/channels/chan-1/messages"),
    ]


def test_transport_raises_when_the_attempt_cap_is_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: every permitted attempt is rate-limited.
    requests = _install(monkeypatch, [_rate_limited("0.1"), _rate_limited("0.2")])
    sleeps: list[float] = []
    transport = DiscordTransport("t", "9", sleeper=sleeps.append, max_attempts=2)

    # When/Then: the cap is honored — the tick fails loudly instead of spinning.
    with pytest.raises(Exception) as raised:
        _ = transport.api("GET", "/channels/chan-1")
    assert getattr(raised.value, "code", None) == 429
    assert sleeps == [0.1]
    assert len(requests) == 2


def test_transport_rejects_a_response_that_omits_the_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a 200 whose body has no usable id.
    _install(monkeypatch, [_ok(b"{}")])
    transport = DiscordTransport("t", "9")

    # When/Then: an unusable binding is refused rather than stored empty.
    with pytest.raises(DiscordTransportError):
        _ = transport.post_message("chan-1", "hello")


def test_transport_refuses_a_non_positive_attempt_budget() -> None:
    # Given/When/Then: zero attempts would silently never call Discord at all.
    with pytest.raises(DiscordTransportError):
        _ = DiscordTransport("t", "9", max_attempts=0)


def test_transport_raises_a_server_error_immediately_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Discord answers with a non-rate-limit server error.
    requests = _install(monkeypatch, [_Response(500, "server error", Message(), b"")])
    sleeps: list[float] = []
    transport = DiscordTransport("t", "9", sleeper=sleeps.append, max_attempts=3)

    # When/Then: only 429 is retryable — retrying a 500 would re-post the same card.
    with pytest.raises(Exception) as raised:
        _ = transport.api("POST", "/channels/chan-1/messages", {"content": "x"})
    assert getattr(raised.value, "code", None) == 500
    assert sleeps == []
    assert len(requests) == 1


def test_transport_uses_a_conservative_delay_when_429_omits_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a rate-limit response with no Retry-After header at all.
    _install(monkeypatch, [_Response(429, "rate limited", Message(), b""), _ok(b"{}")])
    sleeps: list[float] = []
    transport = DiscordTransport("t", "9", sleeper=sleeps.append, max_attempts=2)

    # When: the call retries.
    _ = transport.api("GET", "/channels/chan-1")

    # Then: it still backs off rather than hammering Discord immediately.
    assert sleeps == [1.0]
