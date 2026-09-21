"""Explicit v2 notices pass through the real facades without overriding old callers."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Never

import pytest

from automation import owner_notice
from automation.interop import origin_notice
from automation.interop.owner_message import Action, OwnerMessage, Periodic, Ref, Result, render

HERE = Ref("self")
WHEN = datetime(2030, 1, 2, tzinfo=UTC)
MESSAGE = OwnerMessage(
    "N42", "S42", "F42", HERE, Action("none"), None, "not_applicable",
    Result("executed"), render_version="owner-ko-v2",
)


def test_result_v2_when_origin_facade_uses_fallback() -> None:
    # Given a result with no origin, using the existing injected fallback seam.
    message = replace(MESSAGE, detail=Result("executed"))
    sent: list[str] = []

    def unused(*_args: str) -> Never:
        pytest.fail("No origin must cause no Discord lookup")

    # When
    _ = origin_notice.deliver(
        api=unused, transport_factory=unused, record={}, thread_name="unused",
        content="legacy", fallback=sent.append, message=message,
        fallback_destination=HERE,
    )
    # Then the real facade sends exactly the selected presentation.
    assert sent == [render(message, destination=HERE)]


def test_periodic_v2_when_owner_facade_resolves_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a real owner facade with only the network sender substituted.
    message = replace(MESSAGE, detail=Periodic(WHEN, WHEN))
    sent: list[str] = []

    def target(_token: str) -> str:
        return "222"

    def send(_token: str, _channel: str, body: str) -> None:
        sent.append(body)

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic")
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda: "222")
    monkeypatch.setattr(owner_notice, "resolve_notice_target", target)
    monkeypatch.setattr(owner_notice, "send_notice", send)
    # When
    delivered = owner_notice.notify_owner("legacy", message=message)
    # Then
    assert delivered
    assert sent == [render(message, destination=HERE)]
