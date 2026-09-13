"""Owner-message rendering: parsed fields, references, and typed variant coverage.

Korean copy is reviewed in manual QA; tests do not pin natural-language prose.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime
from itertools import product
from typing import Final, Literal

import pytest

from automation.interop.owner_message import (
    Action, Approval, OwnerMessage, OwnerMessageError, Periodic, Ref, Result, Space, render,
)

_SOURCE: Final = Ref("message", "guild", "111", "222", "333")
_DESTINATION: Final = Ref("channel", "dm", channel_id="999")
_WHEN: Final = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
_MESSAGE: Final = OwnerMessage(
    "M42", "S42", "F42", _SOURCE, Action("none"), None,
    "not_applicable", Approval(_WHEN, "C42"),
)


def test_five_fields_when_approval_has_complete_data() -> None:
    # Given
    message = _MESSAGE
    # When
    body = render(message, destination=_DESTINATION)
    # Then
    rows = [line.partition(": ") for line in body.splitlines()]
    assert len(rows) == 5
    assert all(label and separator and value for label, separator, value in rows)
    assert all(value in body for value in (message.subject_key, message.subject, message.fact))
    assert _WHEN.isoformat() in rows[1][2]
    assert "C42" in rows[4][2]


def test_identical_bytes_when_input_is_repeated() -> None:
    # Given
    message = _MESSAGE
    # When
    outputs = {render(message, destination=_DESTINATION).encode("utf-8") for _ in range(8)}
    # Then
    assert len(outputs) == 1


@pytest.mark.parametrize("location,urls", [
    (_SOURCE, []), (replace(_SOURCE, scope="channel"), []), (Ref("self"), []), (Ref("none"), []),
    (Ref("resource", url="https://example.test/item"), ["https://example.test/item"]),
])
def test_reference_urls_when_destination_equals_location(location: Ref, urls: list[str]) -> None:
    # Given
    message = replace(_MESSAGE, location=location)
    # When
    body = render(message, destination=location)
    # Then: identical resource metadata never makes the resource local.
    assert [word for word in body.split() if word.startswith("http")] == urls


@pytest.mark.parametrize("scope", ["message", "channel"])
def test_local_reference_when_only_channel_coordinates_match(
    scope: Literal["message", "channel"],
) -> None:
    # Given
    message = replace(_MESSAGE, location=replace(_SOURCE, scope=scope))
    destination = Ref("channel", channel_id="222")
    # When
    body = render(message, destination=destination)
    # Then
    assert "discord.com" not in body
    assert body.splitlines()[2].partition(": ")[2]


def test_one_link_when_action_targets_share_the_remote_location() -> None:
    # Given
    target = replace(_SOURCE, search=("L42", "K42"))
    message = replace(
        _MESSAGE, owner=Action("react", target, "A42"),
        recovery=Action("reply", target, "R42"), agent_next="N42",
    )
    # When
    body = render(message, destination=_DESTINATION)
    # Then
    assert body.count("https://discord.com/channels/111/222/333") == 1
    assert all(value in body for value in ("A42", "R42", "N42"))


def test_search_fallback_when_space_is_unknown() -> None:
    # Given
    message = replace(_MESSAGE, location=replace(_SOURCE, space="unknown", search=("L42", "K42")))
    # When
    body = render(message, destination=_DESTINATION)
    # Then
    assert "discord.com" not in body and "@me" not in body
    assert "L42" in body and "K42" in body


def test_explicit_handoff_values_when_both_actors_have_no_action() -> None:
    # Given
    message = replace(_MESSAGE, owner=Action("none", _SOURCE, "IGNORED42"), agent_next=None)
    # When
    body = render(message, destination=_DESTINATION)
    # Then
    handoff = body.splitlines()[3].partition(": ")[2].split("; ")
    assert len(handoff) == 2
    assert all(part.partition(": ")[2].strip() for part in handoff)
    assert "IGNORED42" not in body


@pytest.mark.parametrize("verb", ["react", "reply", "open"])
def test_action_reference_when_target_differs_from_location(
    verb: Literal["react", "reply", "open"],
) -> None:
    # Given
    target = Ref("message", "dm", channel_id="888", message_id="777")
    message = replace(_MESSAGE, owner=Action(verb, target, "A42"))
    # When
    body = render(message, destination=_DESTINATION)
    # Then
    assert "https://discord.com/channels/@me/888/777" in body.splitlines()[3]
    assert "A42" in body.splitlines()[3]


def test_distinct_facts_when_result_outcomes_differ() -> None:
    # Given
    details = (Result("executed"), Result("cancelled"), Result("expired"))
    # When
    facts = {render(replace(_MESSAGE, detail=detail), destination=_DESTINATION).splitlines()[1]
             for detail in details}
    # Then
    assert len(facts) == 3


def test_observation_interval_when_detail_is_periodic() -> None:
    # Given
    end = datetime(2026, 1, 3, 4, 5)
    message = replace(_MESSAGE, detail=Periodic(_WHEN, end))
    # When
    body = render(message, destination=_DESTINATION)
    # Then
    assert _WHEN.isoformat() in body.splitlines()[1]
    assert end.isoformat() in body.splitlines()[1]


@pytest.mark.parametrize("message", [
    replace(_MESSAGE, render_version="future"), replace(_MESSAGE, render_version=""),
    replace(_MESSAGE, contract_version=0), replace(_MESSAGE, contract_version=2),
])
def test_version_refusal_when_contract_or_renderer_is_unsupported(message: OwnerMessage) -> None:
    # Given / When / Then
    with pytest.raises(OwnerMessageError) as error:
        _ = render(message, destination=_DESTINATION)
    assert error.value.contract_version == message.contract_version
    assert error.value.render_version == message.render_version


def test_contract_shape_when_envelope_is_constructed() -> None:
    # Given
    message = _MESSAGE
    # When
    names = tuple(field.name for field in fields(message))
    # Then
    assert names == ("subject_key", "subject", "fact", "location", "owner", "agent_next",
                     "recovery", "detail", "contract_version", "render_version")
    assert message.contract_version == 1 and message.render_version == "owner-ko-v1"


@pytest.mark.parametrize("value", [_SOURCE, Action("none"), Approval(None, ""),
                                 Result("expired"), Periodic(_WHEN, _WHEN), _MESSAGE])
def test_frozen_slotted_contract_when_mutation_is_attempted(
    value: Ref | Action | Approval | Result | Periodic | OwnerMessage,
) -> None:
    # Given
    field = fields(value)[0].name
    # When / Then: dynamic setattr deliberately exercises dataclass immutability.
    with pytest.raises(FrozenInstanceError):
        setattr(value, field, "CHANGED42")
    assert not hasattr(value, "__dict__")


@pytest.mark.parametrize("scope", ["none", "self"])
def test_local_scope_when_destination_is_elsewhere(scope: Literal["none", "self"]) -> None:
    # Given
    message = replace(_MESSAGE, location=replace(_SOURCE, scope=scope))
    # When
    body = render(message, destination=_DESTINATION)
    # Then
    assert "http" not in body
    assert body.splitlines()[2].partition(": ")[2]


def test_channel_link_when_reference_also_contains_message_coordinates() -> None:
    # Given
    message = replace(_MESSAGE, location=replace(_SOURCE, scope="channel"))
    # When
    body = render(message, destination=_DESTINATION)
    # Then
    assert body.splitlines()[2].partition(": ")[2] == "https://discord.com/channels/111/222"


@pytest.mark.parametrize("channel", [None, "", " \t"])
def test_search_fallback_when_remote_channel_values_are_missing_or_invalid(channel: str | None) -> None:
    # Given
    location = Ref("message", channel_id=channel, search=("L42", "K42"))
    message = replace(_MESSAGE, location=location)
    # When
    body = render(message, destination=_DESTINATION)
    # Then
    assert "L42" in body and "K42" in body
    assert "http" not in body


@pytest.mark.parametrize("url", [None, "", " ", "https://[", "https://example.test:bad",
    "https://example.test:70000", "https://example.test:0", "https://example.test/\x00",
    "\nhttps://example.test", "https://example.test/a b", "https://example.test\\path",
    "https://user:password@example.test", "https://", "javascript:void(0)"])
def test_resource_search_when_explicit_url_cannot_be_used(url: str | None) -> None:
    # Given
    message = replace(_MESSAGE, location=Ref("resource", url=url, search=("L42", "K42")))
    # When
    body = render(message, destination=_DESTINATION)
    # Then
    assert "http" not in body and "javascript:" not in body
    assert "L42" in body and "K42" in body


@pytest.mark.parametrize("url", ["http://example.test", "https://example.test/p?q=1#section",
                                 "https://[::1]:8443/a"])
def test_resource_link_when_explicit_url_is_usable(url: str) -> None:
    # Given: resource URLs are independent of channel-locality metadata.
    message = replace(_MESSAGE, location=Ref("resource", channel_id="888", url=url))
    # When
    body = render(message, destination=_DESTINATION)
    # Then
    assert body.count(url) == 1


def test_distinct_recovery_when_support_differs() -> None:
    # Given
    recoveries: tuple[Literal["not_applicable", "irreversible"] | Action, ...] = (
        "not_applicable", "irreversible", Action("reply", Ref("self"), "R42"),
    )
    # When
    lines = {render(replace(_MESSAGE, recovery=value), destination=_DESTINATION).splitlines()[4]
             for value in recoveries}
    # Then
    assert len(lines) == 3
    assert all(line.partition(": ")[2].strip() for line in lines)


@pytest.mark.parametrize("text", ["", " \t", "X42\r\nY42\u2028Z42", "S" * 5000],
                         ids=["empty", "blank", "multiline", "long"])
def test_five_lines_when_producer_text_is_blank_or_multiline(text: str) -> None:
    # Given
    message = OwnerMessage(text, text, text, Ref("none"), Action("reply", None, text),
                           text, Action("none"), Approval(None, text))
    # When
    body = render(message, destination=_DESTINATION)
    # Then
    assert len(body.splitlines()) == 5
    assert all(part in body for part in text.split())


def test_total_five_line_render_when_field_combinations_are_adversarial() -> None:
    # Given: exhaustive products, no clock, randomness, I/O, or scheduling.
    scopes: tuple[Literal["self", "channel", "message", "resource", "none"], ...] = (
        "self", "channel", "message", "resource", "none",
    )
    spaces: tuple[Space, ...] = ("guild", "dm", "unknown")
    verbs: tuple[Literal["none", "react", "reply", "open"], ...] = ("none", "react", "reply", "open")
    references = tuple(Ref(scope, space, value, value, value, url, search)
        for scope, space, value, url, search in product(
            scopes, spaces, (None, "", " \t", "bad", "0", "２", "222"),
            (None, "", "https://[", "https://example.test/item"), (None, ("L42", "K42")),
        ))
    actions = tuple(Action(verb, target, argument) for verb, target, argument in product(
        verbs, (None, _SOURCE), (None, "", " \n ", "A42"),
    ))
    details = (Approval(None, ""), Approval(_WHEN, "C42"), Result("executed"),
               Result("cancelled"), Result("expired"), Periodic(datetime.min, datetime.max))
    for location, action, detail in product(references, actions, details):
        message = replace(_MESSAGE, subject="S" * 5000, fact=" F42\r\nF43 ",
                          location=location, owner=action, recovery=action, detail=detail)
        # When
        body = render(message, destination=_DESTINATION)
        # Then
        assert len(body.splitlines()) == 5
        assert "S" * 5000 in body
        assert "https://[" not in body
        assert all(url in {"https://discord.com/channels/222/222",
                          "https://discord.com/channels/222/222/222",
                          "https://discord.com/channels/@me/222",
                          "https://discord.com/channels/@me/222/222",
                          "https://discord.com/channels/111/222/333",
                          "https://example.test/item"}
                   for url in body.split() if url.startswith("http"))
