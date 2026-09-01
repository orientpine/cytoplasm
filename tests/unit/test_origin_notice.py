"""RED-first contract for the shared origin-thread result-notice helper.

Owner instruction 2026-08-23: EVERY approval-gated skill routes its RESULT
notices (sent/cancelled/executed) back to the channel the instruction came
from, in a thread; the approval surface (owner DM) stays approval-only.
mail shipped the first implementation privately (triage_confirm.notify_result);
this module generalizes it so the other skills reuse ONE implementation
instead of growing per-skill copies.

The helper is injection-only: callers pass their own Discord api callable and
chunking transport factory, so each skill's existing test monkeypatch points
(`_api`, `_dm_transport`, `dm_owner`) keep working unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from automation.interop.origin_notice import (  # noqa: E402
    OriginRef,
    deliver,
    resolve_thread_id,
)

ORIGIN_CHANNEL = "200000000000000001"
ORIGIN_MESSAGE = "origin-message-1"
THREAD_ID = "300000000000000001"


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://discord.test", code, "boom", None, None)


class _Transport:
    def __init__(self, channel_id: str, log: list[tuple[str, str]]) -> None:
        self.channel_id = channel_id
        self.log = log

    def send(self, body: str) -> tuple[SimpleNamespace, ...]:
        self.log.append((self.channel_id, body))
        return (SimpleNamespace(message_id="thread-post-1"),)


# ------------------------------------------------------------------ OriginRef

def test_of_record_reads_origin_fields_and_truthiness() -> None:
    # Given: records with and without the origin binding
    bound = OriginRef.of_record(
        {"origin_channel_id": ORIGIN_CHANNEL, "origin_message_id": ORIGIN_MESSAGE}
    )
    channel_only = OriginRef.of_record({"origin_channel_id": ORIGIN_CHANNEL})
    legacy = OriginRef.of_record({"id": "abc123"})
    null_fields = OriginRef.of_record({"origin_channel_id": None, "origin_message_id": None})
    # Then: the ref is truthy exactly when a channel is bound
    assert (bound.channel_id, bound.message_id) == (ORIGIN_CHANNEL, ORIGIN_MESSAGE)
    assert bool(bound) and bool(channel_only)
    assert not legacy and not null_fields


# ------------------------------------------------------------ resolve_thread_id

def test_resolve_creates_message_anchored_thread() -> None:
    # Given: an origin with the instruction message id
    calls: list[tuple[str, str, dict | None]] = []

    def api(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        return {"id": THREAD_ID}

    # When: the thread is resolved
    thread = resolve_thread_id(api, OriginRef(ORIGIN_CHANNEL, ORIGIN_MESSAGE), "스레드 이름")
    # Then: it is anchored on the instruction message
    assert thread == THREAD_ID
    assert calls == [
        (
            "POST",
            f"/channels/{ORIGIN_CHANNEL}/messages/{ORIGIN_MESSAGE}/threads",
            {"name": "스레드 이름"},
        )
    ]


def test_resolve_reuses_existing_thread_on_400() -> None:
    # Given: Discord refuses because the message already carries a thread
    def api(method: str, path: str, payload: dict | None = None):
        raise _http_error(400)

    # When/Then: the existing thread is reused — its id equals the message id
    thread = resolve_thread_id(api, OriginRef(ORIGIN_CHANNEL, ORIGIN_MESSAGE), "이름")
    assert thread == ORIGIN_MESSAGE


def test_resolve_raises_on_other_http_errors() -> None:
    # Given: a non-400 Discord failure (e.g. missing permission)
    def api(method: str, path: str, payload: dict | None = None):
        raise _http_error(403)

    # When/Then: the failure surfaces to the caller (deliver handles fallback)
    with pytest.raises(HTTPError):
        resolve_thread_id(api, OriginRef(ORIGIN_CHANNEL, ORIGIN_MESSAGE), "이름")


def test_resolve_creates_channel_thread_without_message() -> None:
    # Given: only the origin channel is known
    calls: list[tuple[str, str, dict | None]] = []

    def api(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        return {"id": THREAD_ID}

    # When: the thread is resolved
    thread = resolve_thread_id(api, OriginRef(ORIGIN_CHANNEL), "이름")
    # Then: a public thread is created in the channel itself
    assert thread == THREAD_ID
    [(method, path, payload)] = calls
    assert (method, path) == ("POST", f"/channels/{ORIGIN_CHANNEL}/threads")
    assert payload is not None and payload["name"] == "이름" and payload["type"] == 11


# ------------------------------------------------------------------- deliver

def test_deliver_falls_back_to_owner_without_origin() -> None:
    # Given: a legacy record with no origin binding
    fallback_log: list[str] = []

    def deny_api(method: str, path: str, payload: dict | None = None):
        raise AssertionError(f"network call: {method} {path}")

    # When: the result is delivered
    result = deliver(
        api=deny_api,
        transport_factory=lambda channel_id: pytest.fail("no thread transport expected"),
        record={"id": "abc123"},
        thread_name="이름",
        content="결과",
        fallback=lambda content: fallback_log.append(content) or "dm-1",
    )
    # Then: only the owner fallback fires, with the unchanged content
    assert result == "dm-1"
    assert fallback_log == ["결과"]


def test_deliver_posts_content_to_origin_thread() -> None:
    # Given: an origin-bound record and a healthy thread path
    posts: list[tuple[str, str]] = []

    def api(method: str, path: str, payload: dict | None = None):
        return {"id": THREAD_ID}

    # When: the result is delivered
    result = deliver(
        api=api,
        transport_factory=lambda channel_id: _Transport(channel_id, posts),
        record={
            "id": "abc123",
            "origin_channel_id": ORIGIN_CHANNEL,
            "origin_message_id": ORIGIN_MESSAGE,
        },
        thread_name="이름",
        content="✉️ 발송 완료",
        fallback=lambda content: pytest.fail("fallback must not fire on success"),
    )
    # Then: the content lands in the resolved thread through the chunking transport
    assert result == "thread-post-1"
    assert posts == [(THREAD_ID, "✉️ 발송 완료")]


def test_deliver_marks_and_falls_back_on_thread_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an origin-bound record whose thread creation blows up
    fallback_log: list[str] = []

    def api(method: str, path: str, payload: dict | None = None):
        raise RuntimeError("thread API down")

    # When: the result is delivered
    result = deliver(
        api=api,
        transport_factory=lambda channel_id: pytest.fail("no thread transport expected"),
        record={"id": "abc123", "origin_channel_id": ORIGIN_CHANNEL},
        thread_name="이름",
        content="결과",
        fallback=lambda content: fallback_log.append(content) or "dm-1",
    )
    # Then: the owner still gets the result and the failure leaves a marker
    assert result == "dm-1"
    assert fallback_log == ["결과"]
    err = capsys.readouterr().err
    assert "NOTIFY-THREAD-FAIL" in err and "abc123" in err
