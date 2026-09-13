"""Runtime boundary probes; corrupt only disposable frozen fixtures, never API types."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, tzinfo
from itertools import product
from typing import Final, Literal, TypeAlias, assert_never, override

import pytest

from automation.interop.owner_message import (
    Action, Approval, LinkStatus, OwnerMessage, OwnerMessageError, Periodic, Ref,
    Result, Space, discord_link, render,
)

_REF: Final = Ref("message", "guild", "111", "222", "333", search=("L42", "K42"))
_MESSAGE: Final = OwnerMessage("M42", "S42", "F42", _REF, Action("none", _REF), None,
                               Action("none", _REF), Result("executed"))
_DESTINATION: Final = Ref("channel", "dm", channel_id="999")
_VALUES: Final = (1, -1, None, list[str](), dict[str, str](), b"222", object(), " \t", "X" * 5000,
                  ("K42",), ("L42", 1), ["L42", "K42"])


@dataclass(frozen=True, slots=True)
class Invocation:
    message: OwnerMessage = _MESSAGE
    destination: Ref = _DESTINATION


@dataclass(frozen=True, slots=True)
class LinkInput:
    space: Space = "guild"
    channel_id: str | None = "222"
    message_id: str | None = "333"
    guild_id: str | None = "111"


Contract: TypeAlias = OwnerMessage | Ref | Action | Approval | Result | Periodic


@pytest.mark.parametrize("field,invocation", [
    (field, replace(Invocation(), **{field: value}))
    for field, value in product(("message", "destination"), _VALUES)
])
def test_typed_refusal_when_top_level_value_is_malformed(field: str, invocation: Invocation) -> None:
    # Given: replace intentionally bypasses constructor annotations at this boundary.
    # When / Then
    with pytest.raises(OwnerMessageError) as caught:
        _ = render(invocation.message, destination=invocation.destination)
    assert caught.value.detail == field


def test_typed_refusal_when_every_runtime_field_is_fuzzed() -> None:
    # Given: each field of every contract model, in every reachable nesting position.
    fixtures: tuple[tuple[str, Contract, Callable[[Contract], Invocation]], ...] = (
        ("message", _MESSAGE, lambda value: replace(Invocation(), message=value)),
        ("destination", _DESTINATION, lambda value: replace(Invocation(), destination=value)),
        ("message.location", _REF, lambda value: Invocation(replace(_MESSAGE, location=value))),
        ("message.owner", Action("react", _REF, "A42"), lambda value: Invocation(replace(_MESSAGE, owner=value))),
        ("message.recovery", Action("reply", _REF, "R42"), lambda value: Invocation(replace(_MESSAGE, recovery=value))),
        ("message.owner.target", _REF, lambda value: Invocation(replace(_MESSAGE, owner=replace(Action("none"), target=value)))),
        ("message.recovery.target", _REF, lambda value: Invocation(replace(_MESSAGE, recovery=replace(Action("none"), target=value)))),
        ("message.detail", Approval(None, "C42"), lambda value: Invocation(replace(_MESSAGE, detail=value))),
        ("message.detail", Result("executed"), lambda value: Invocation(replace(_MESSAGE, detail=value))),
        ("message.detail", Periodic(datetime.min, datetime.max), lambda value: Invocation(replace(_MESSAGE, detail=value))),
    )
    optional = {"guild_id", "channel_id", "message_id", "url", "search", "target",
                "argument", "expires_at", "agent_next"}
    text = {"subject_key", "subject", "fact", "agent_next", "guild_id", "channel_id",
            "message_id", "url", "argument", "cancel_effect"}
    failures: list[str] = []
    exceptions: Counter[str] = Counter()
    accepted = 0
    for (path, fixture, wrap), value in product(fixtures, _VALUES):
        for field in fields(fixture):
            invocation = wrap(replace(fixture, **{field.name: value}))
            expected = f"{path}.{field.name}"
            valid = ((value is None and field.name in optional)
                     or (type(value) is str and field.name in text)
                     or (type(value) is int and value == 1 and field.name == "contract_version"))
            # When: exception counting is the behavior under this adversarial test.
            try:
                body = render(invocation.message, destination=invocation.destination)
            except (AssertionError, AttributeError, TypeError, ValueError) as error:
                exceptions[type(error).__name__] += 1
                if valid or not isinstance(error, OwnerMessageError):
                    failures.append(f"{expected}: {type(error).__name__}")
                elif getattr(error, "detail", None) != expected:
                    failures.append(f"{expected}: wrong detail")
            else:
                accepted += 1
                if not valid or len(body.splitlines()) != 5:
                    failures.append(f"{expected}: accepted malformed input")
                urls = [word for word in body.split() if word.startswith("https://")]
                if any(url not in {"https://discord.com/channels/111/222/333",
                                   "https://discord.com/channels/111/222"} for url in urls):
                    failures.append(f"{expected}: malformed URL")
    # Then
    print(f"runtime render: accepted={accepted}, exceptions={dict(exceptions)}, failures={len(failures)}")
    assert failures == []


def test_invalid_result_when_link_runtime_arguments_are_malformed() -> None:
    # Given: direct API invocation retains the published signature; corrupted fixtures
    # represent dynamic callers that do not run a static type checker.
    failures: list[str] = []
    exceptions: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    for space, value, field in product(("guild", "dm", "unknown"), _VALUES, fields(LinkInput)):
        data = replace(LinkInput(), space=space, **{field.name: value}) if field.name != "space" else replace(LinkInput(), space=value)
        # When
        try:
            result = discord_link(space=data.space, channel_id=data.channel_id,
                                  message_id=data.message_id, guild_id=data.guild_id)
        except (AssertionError, AttributeError, TypeError, ValueError) as error:
            exceptions[type(error).__name__] += 1
        else:
            statuses[result.status.value] += 1
            expected = LinkStatus.INVALID
            if data.space == "unknown" or (value is None and field.name == "channel_id"):
                expected = LinkStatus.UNAVAILABLE
            if value is None and field.name == "guild_id" and data.space == "guild":
                expected = LinkStatus.UNAVAILABLE
            if value is None and (field.name == "message_id" or (field.name == "guild_id" and data.space == "dm")) and data.space != "unknown":
                expected = LinkStatus.AVAILABLE
            if result.status != expected or (result.url is None and not result.detail):
                failures.append(f"{field.name}: {result.status} != {expected}")
    # Then
    print(f"runtime link: statuses={dict(statuses)}, exceptions={dict(exceptions)}")
    assert exceptions == {} and failures == []


def test_deleted_is_never_inferred_when_builder_only_has_coordinates() -> None:
    # Given: whole declared coordinate fuzz domain, without lifecycle lookup.
    values = (None, "", " \t", "-1", "0", "２", "222", "1" * 5000)
    # When
    statuses = {discord_link(space=space, channel_id=channel, message_id=message, guild_id=guild).status
                for space, channel, message, guild in product(("guild", "dm", "unknown"), values, values, values)}
    # Then
    assert LinkStatus.DELETED not in statuses
    assert statuses == {LinkStatus.AVAILABLE, LinkStatus.UNAVAILABLE, LinkStatus.INVALID}


@pytest.mark.parametrize("scope", ["channel", "message", "resource"])
@pytest.mark.parametrize("channel", [None, "", " \t", "222"])
def test_reference_when_channel_values_match_regardless_of_metadata(
    scope: Literal["channel", "message", "resource"], channel: str | None,
) -> None:
    # Given
    location = replace(_REF, scope=scope, channel_id=channel, url="https://example.test/item")
    message = replace(_MESSAGE, location=location)
    destination = replace(_DESTINATION, channel_id=channel, message_id="777")
    # When
    body = render(message, destination=destination)
    # Then: retain the full old input matrix with the corrected D3 outcomes.
    match scope:
        case "resource":
            assert body.count("https://example.test/item") == 1
            assert "K42" not in body
        case "channel" | "message":
            assert "https://" not in body
            assert ("K42" in body) == (channel is None)
        case _:
            assert_never(scope)


def test_exact_remote_url_when_destination_channel_differs() -> None:
    # Given
    destination = Ref("channel", "guild", guild_id="111", channel_id="888")
    # When
    body = render(_MESSAGE, destination=destination)
    # Then
    assert body.count("https://discord.com/channels/111/222/333") == 1


@dataclass(frozen=True, slots=True)
class RaisingTimezone(tzinfo):
    error_type: type[BaseException]

    @override
    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        raise self.error_type()


def test_field_refusal_when_datetime_timezone_formatting_raises() -> None:
    # Given: exact datetime values can invoke arbitrary producer tzinfo code.
    failures: list[str] = []
    exceptions: Counter[str] = Counter()
    zones = (tzinfo(), *(RaisingTimezone(error) for error in (
        ValueError, RuntimeError, OSError, KeyboardInterrupt, SystemExit, BaseException,
    )))
    for zone, field in product(zones, ("expires_at", "start", "end")):
        value = datetime(2026, 1, 2, tzinfo=zone)
        detail = Approval(value, "C42") if field == "expires_at" else replace(
            Periodic(datetime.min, datetime.max), **{field: value})
        message = replace(_MESSAGE, detail=detail)
        # When: count every escaping class, including producer-raised BaseException.
        try:
            _ = render(message, destination=_DESTINATION)
        except BaseException as error:  # Boundary exception-counting probe, not error suppression.
            exceptions[type(error).__name__] += 1
            if not isinstance(error, OwnerMessageError) or error.detail != f"message.detail.{field}":
                failures.append(f"{field}: {type(error).__name__}")
        else:
            failures.append(f"{field}: accepted failed formatting")
    # Then
    print(f"datetime boundary: exceptions={dict(exceptions)}, failures={len(failures)}")
    assert failures == []


def test_field_refusal_when_contract_slots_are_uninitialized() -> None:
    # Given: every reachable model, wholly unset and with each individual slot missing.
    fixtures: tuple[tuple[str, Contract, Callable[[Contract], Invocation]], ...] = (
        ("message", _MESSAGE, lambda value: replace(Invocation(), message=value)),
        ("destination", _DESTINATION, lambda value: replace(Invocation(), destination=value)),
        ("message.location", _REF, lambda value: Invocation(replace(_MESSAGE, location=value))),
        ("message.owner", _MESSAGE.owner, lambda value: Invocation(replace(_MESSAGE, owner=value))),
        ("message.recovery", Action("none", _REF), lambda value: Invocation(replace(_MESSAGE, recovery=value))),
        ("message.owner.target", _REF, lambda value: Invocation(replace(_MESSAGE, owner=replace(Action("none"), target=value)))),
        ("message.recovery.target", _REF, lambda value: Invocation(replace(_MESSAGE, recovery=replace(Action("none"), target=value)))),
        ("message.detail", Approval(None, "C42"), lambda value: Invocation(replace(_MESSAGE, detail=value))),
        ("message.detail", Result("executed"), lambda value: Invocation(replace(_MESSAGE, detail=value))),
        ("message.detail", Periodic(datetime.min, datetime.max), lambda value: Invocation(replace(_MESSAGE, detail=value))),
    )
    failures: list[str] = []
    exceptions: Counter[str] = Counter()
    for path, fixture, wrap in fixtures:
        for slot in (None, *fields(fixture)):
            value = object.__new__(type(fixture)) if slot is None else replace(fixture)
            if slot is not None:
                object.__delattr__(value, slot.name)
            invocation = wrap(value)
            expected = f"{path}.{fields(fixture)[0].name if slot is None else slot.name}"
            # When
            try:
                _ = render(invocation.message, destination=invocation.destination)
            except BaseException as error:  # Boundary exception-counting probe, not error suppression.
                exceptions[type(error).__name__] += 1
                if not isinstance(error, OwnerMessageError) or error.detail != expected:
                    failures.append(f"{expected}: {type(error).__name__}")
            else:
                failures.append(f"{expected}: accepted missing slot")
    # Then
    print(f"slot boundary: exceptions={dict(exceptions)}, failures={len(failures)}")
    assert failures == []
