"""Destination-aware delivery contract; legacy tests stay untouched in their own file."""
from __future__ import annotations

from dataclasses import replace
from inspect import signature

import pytest

from automation.interop import origin_notice
from automation.interop.owner_message import Action, OwnerMessage, Ref, Result, render
from test_origin_notice_compatibility import DeliverySurface

RECORD = {"id": "request", "approval_guild_id": "111", "approval_thread_id": "222",
          "message_id": "333"}
LOCATION = Ref(scope="message", space="guild", guild_id="111", channel_id="222",
               message_id="333", search=("request", "lookup-555"))
MESSAGE = OwnerMessage(
    subject_key="request", subject="result", fact="processed", location=LOCATION,
    owner=Action("open", LOCATION), agent_next=None,
    recovery="not_applicable", detail=Result("executed"),
)
THREAD = Ref(scope="channel", space="guild", guild_id="111", channel_id="222")


def envelope_options(message: OwnerMessage, destination: Ref | None = None) -> dict:
    """Use the same additive capability negotiation as migrating callers."""
    if getattr(origin_notice, "ACCEPTS_OWNER_MESSAGE", False):
        return {"message": message, "fallback_destination": destination}
    return {}


def test_capability_when_runtime_supports_owner_messages():
    # Given / When: a migrated caller inspects this runtime.
    parameters = signature(origin_notice.deliver).parameters
    # Then: the capability advertises both additive keyword-only parameters.
    assert getattr(origin_notice, "ACCEPTS_OWNER_MESSAGE", False) is True
    for name in ("message", "fallback_destination"):
        assert parameters[name].kind == parameters[name].KEYWORD_ONLY
        assert parameters[name].default is None


@pytest.mark.parametrize("origin", [False, True])
def test_locator_and_receipt_when_envelope_falls_back(monkeypatch, capsys, origin):
    # Given: the plain text was composed for the approval thread, not the fallback.
    surface = DeliverySurface(monkeypatch)
    surface.fail_send = True
    # When: the thread fails with HTTPError or the record has no origin at all.
    result = origin_notice.deliver(
        api=surface.api, transport_factory=surface.transport, record=RECORD if origin else {},
        thread_name="request", content=render(MESSAGE, destination=THREAD),
        fallback=surface.fallback, outcome=origin_notice.ThreadOutcome.DONE,
        **envelope_options(MESSAGE),
    )
    # Then: fallback re-renders for ELSEWHERE and keeps the exact fallback return value.
    [body] = surface.fallbacks
    assert body.count("https://discord.com/channels/111/222/333") == 1
    assert body == render(MESSAGE, destination=Ref(scope="none"))
    assert result is surface.receipt
    assert surface.calls == []
    assert capsys.readouterr().err == (
        "NOTIFY-THREAD-FAIL id=request err=HTTPError\n" if origin else ""
    )


@pytest.mark.parametrize("origin", [False, True])
def test_locality_when_fallback_explicitly_uses_same_thread(monkeypatch, origin):
    # Given: a fallback whose destination is deliberately the original thread.
    surface = DeliverySurface(monkeypatch)
    surface.fail_send = True
    # When: either fallback path is taken.
    origin_notice.deliver(
        api=surface.api, transport_factory=surface.transport, record=RECORD if origin else {},
        thread_name="request", content="legacy", fallback=surface.fallback,
        **envelope_options(MESSAGE, THREAD),
    )
    # Then: rendering follows the explicit destination, without a self-link.
    assert surface.fallbacks == [render(MESSAGE, destination=THREAD)]
    assert "discord.com" not in surface.fallbacks[0]


@pytest.mark.parametrize("record", [RECORD, {"origin_channel_id": "777"},
                                     {"origin_channel_id": "777", "origin_message_id": "888"}])
def test_locality_when_thread_resolution_succeeds(monkeypatch, record):
    # Given: approval-thread, channel-thread and message-anchor resolution paths.
    surface = DeliverySurface(monkeypatch)
    # When: the real transport posts into resolved thread 222.
    result = origin_notice.deliver(
        api=surface.api, transport_factory=surface.transport, record=record,
        thread_name="request", content="legacy", fallback=surface.fallback,
        outcome=origin_notice.ThreadOutcome.DONE, **envelope_options(MESSAGE),
    )
    # Then: the actual destination, not the origin channel, controls locality.
    assert surface.posts == [render(MESSAGE, destination=THREAD)]
    assert "discord.com" not in surface.posts[0]
    assert result == "501"
    assert surface.calls[-1] == (
        "PATCH", "/channels/222", {"archived": True, "name": "✅ 완료 · request"},
    )


@pytest.mark.parametrize("size", [1, 4001])
def test_last_chunk_id_when_envelope_is_split(monkeypatch, size):
    # Given: a message sized to produce either one or multiple real transport chunks.
    surface = DeliverySurface(monkeypatch)
    message = replace(MESSAGE, fact="x" * size)
    # When: an envelope replaces the short legacy text.
    result = origin_notice.deliver(
        api=surface.api, transport_factory=surface.transport, record=RECORD,
        thread_name="request", content="legacy", fallback=surface.fallback,
        **envelope_options(message),
    )
    # Then: chunking still determines the LAST receipt; a single chunk keeps id 501.
    assert len(surface.posts) == (1 if size == 1 else 3)
    assert result == ("501" if size == 1 else "503")


def test_search_key_when_guild_is_missing_on_fallback(monkeypatch):
    # Given: channel/card coordinates are known but the guild is not.
    surface = DeliverySurface(monkeypatch)
    surface.fail_send = True
    location = replace(LOCATION, space="unknown", guild_id=None)
    message = replace(MESSAGE, location=location, owner=Action("open", location))
    # When: the failed thread moves the notice elsewhere.
    origin_notice.deliver(
        api=surface.api, transport_factory=surface.transport,
        record={"approval_thread_id": "222", "message_id": "333"},
        thread_name="request", content="legacy", fallback=surface.fallback,
        **envelope_options(message),
    )
    # Then: no fabricated DM/guild URL replaces the producer's search key.
    [body] = surface.fallbacks
    assert "lookup-555" in body
    assert "@me" not in body and "discord.com" not in body


@pytest.mark.parametrize("record", [RECORD, {}, {"approval_thread_id": 222,
    "approval_guild_id": 111, "message_id": 333}])
def test_approval_location_when_record_has_complete_or_missing_coordinates(record):
    # Given: the existing record shape, including malformed boundary values.
    helper = getattr(origin_notice, "approval_location", None)
    assert callable(helper), "approval_location must be available to migrated callers"
    # When: record metadata becomes a typed reference, without a lookup.
    location = helper(record, search=("request", "lookup-555"))
    # Then: only string coordinates survive; unavailable links keep their search key.
    complete = record == RECORD
    assert location == Ref(
        scope="message", space="guild" if complete else "unknown",
        guild_id="111" if complete else None, channel_id="222" if complete else None,
        message_id="333" if complete else None, search=("request", "lookup-555"),
    )


def test_search_key_when_legacy_record_uses_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: legacy records have neither an approval thread nor an origin.
    surface = DeliverySurface(monkeypatch)
    record = {"id": "D-1"}
    location = origin_notice.approval_location(record, search=("L42", "K42"))
    message = replace(MESSAGE, location=location, owner=Action("open", location))
    # When: exercise the real facade, record adapter and renderer together.
    _ = origin_notice.deliver(
        api=surface.api, transport_factory=surface.transport, record=record,
        thread_name="request", content="legacy", fallback=surface.fallback,
        message=message,
    )
    # Then: the fallback payload retains the producer's locator without guessing a URL.
    [body] = surface.fallbacks
    value = body.splitlines()[2].partition(": ")[2]
    assert "L42" in value and "K42" in value
    assert "http" not in body and "@me" not in body


def test_approval_location_when_card_id_is_explicit():
    # Given: calendar/coordination call it dm_message_id, unlike mail's message_id.
    helper = getattr(origin_notice, "approval_location", None)
    assert callable(helper), "approval_location must be available to migrated callers"
    record = {**RECORD, "dm_message_id": "666"}
    # When: the caller supplies the known approval card instead of guessing aliases.
    location = helper(record, message_id=record["dm_message_id"])
    # Then: an explicit card id takes precedence over the different fallback id.
    assert location.message_id == "666"


def test_close_marker_when_envelope_posts_but_archive_fails(monkeypatch, capsys):
    # Given: the post succeeds and PATCH is denied.
    surface = DeliverySurface(monkeypatch)
    surface.fail_close = True
    # When: a terminal envelope is delivered.
    result = origin_notice.deliver(
        api=surface.api, transport_factory=surface.transport, record=RECORD,
        thread_name="request", content="legacy", fallback=surface.fallback,
        outcome=origin_notice.ThreadOutcome.DONE, **envelope_options(MESSAGE),
    )
    # Then: the success receipt stays and only the existing close marker is emitted.
    assert result == "501" and surface.fallbacks == []
    assert surface.posts == [render(MESSAGE, destination=THREAD)]
    assert capsys.readouterr().err == "THREAD-CLOSE-FAIL id=request thread=222 err=HTTPError\n"
