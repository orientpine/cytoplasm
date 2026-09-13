"""Characterize legacy notices through the real chunking HTTP adapter."""
from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from automation.interop import discord_transport, origin_notice


class DeliverySurface:
    """Per-test HTTP wire recorder; all real chunking and delivery logic stays active."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.posts: list[str] = []
        self.fallbacks: list[str] = []
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail_send = False
        self.fail_close = False
        self.receipt = ("fallback", "444")
        monkeypatch.setattr(discord_transport, "urlopen", self.urlopen)

    def urlopen(self, request: Request, timeout: int) -> BytesIO:
        if self.fail_send:
            raise HTTPError(request.full_url, 403, "denied", None, None)
        assert request.data is not None
        self.posts.append(json.loads(request.data)["content"])
        return BytesIO(json.dumps({"id": str(500 + len(self.posts))}).encode())

    def api(self, method: str, path: str, payload: dict | None = None) -> dict:
        self.calls.append((method, path, payload))
        if self.fail_close and method == "PATCH":
            raise HTTPError("https://discord.test", 403, "denied", None, None)
        return {"id": "222", "name": "request"}

    def transport(self, channel_id: str) -> discord_transport.DiscordTransport:
        return discord_transport.DiscordTransport(token="test", channel_id=channel_id)

    def fallback(self, body: str) -> tuple[str, str]:
        self.fallbacks.append(body)
        return self.receipt


@pytest.mark.parametrize("route", ["thread", "failure", "absent"])
def test_content_bytes_and_receipt_when_using_legacy_delivery(monkeypatch, capsys, route):
    # Given: whitespace and Unicode whose bytes must survive unchanged on every path.
    surface = DeliverySurface(monkeypatch)
    surface.fail_send = route == "failure"
    record = {} if route == "absent" else {"id": "request", "approval_thread_id": "222"}
    content = "  결과 ✅\r\n\tsecond line\n끝  "
    # When: only the existing content contract is used.
    result = origin_notice.deliver(
        api=surface.api, transport_factory=surface.transport, record=record,
        thread_name="request", content=content, fallback=surface.fallback,
        outcome=origin_notice.ThreadOutcome.DONE,
    )
    # Then: exact bytes, return identity, close behavior and markers remain unchanged.
    bodies = surface.posts if route == "thread" else surface.fallbacks
    assert [body.encode() for body in bodies] == [content.encode()]
    if route == "thread":
        assert result == "501"
        assert surface.calls[-1] == (
            "PATCH", "/channels/222", {"archived": True, "name": "✅ 완료 · request"},
        )
    else:
        assert result is surface.receipt
        assert surface.calls == []
    assert capsys.readouterr().err == (
        "NOTIFY-THREAD-FAIL id=request err=HTTPError\n" if route == "failure" else ""
    )


@pytest.mark.parametrize("length, chunks", [(2000, 1), (4001, 3)])
def test_returns_last_chunk_id_when_content_crosses_transport_limit(monkeypatch, length, chunks):
    # Given: the real transport with deterministic wire receipts, not a fake splitter.
    surface = DeliverySurface(monkeypatch)
    # When: a content-only result is posted.
    result = origin_notice.deliver(
        api=surface.api, transport_factory=surface.transport,
        record={"approval_thread_id": "222"}, thread_name="request",
        content="x" * length, fallback=surface.fallback,
    )
    # Then: both single and multiple chunks return the LAST receipt.
    assert len(surface.posts) == chunks
    assert result == str(500 + chunks)


@pytest.mark.parametrize("origin", [False, True])
def test_fallback_error_propagates_when_legacy_delivery_falls_back(monkeypatch, origin):
    # Given: a caller-owned fallback failure, on both fallback paths.
    surface = DeliverySurface(monkeypatch)
    surface.fail_send = True
    error = OSError("fallback unavailable")

    def fallback(body: str) -> str:
        raise error

    # When: the fallback is invoked.
    with pytest.raises(OSError) as raised:
        origin_notice.deliver(
            api=surface.api, transport_factory=surface.transport,
            record={"approval_thread_id": "222"} if origin else {},
            thread_name="request", content="plain", fallback=fallback,
        )
    # Then: the caller still owns the original failure.
    assert raised.value is error
