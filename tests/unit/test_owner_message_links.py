"""Discord coordinate boundary tests for the leaf owner-message link builder."""
from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product
from typing import Final, Literal

import pytest

from automation.interop.owner_message import (
    Action, LinkStatus, OwnerMessage, Ref, Result, Space, discord_link, render,
)

_MESSAGE: Final = OwnerMessage(
    "M42", "S42", "F42", Ref("none"), Action("none"), None,
    "not_applicable", Result("executed"),
)


@dataclass(frozen=True, slots=True)
class LinkCase:
    space: Space
    channel: str | None
    message: str | None = None
    guild: str | None = None


@pytest.mark.parametrize("case,path", [
    (LinkCase("guild", "222", guild="111"), "111/222"),
    (LinkCase("guild", "222", "333", "111"), "111/222/333"),
    (LinkCase("dm", "222"), "@me/222"),
    (LinkCase("dm", "222", "333"), "@me/222/333"),
])
def test_canonical_link_when_required_coordinates_are_available(case: LinkCase, path: str) -> None:
    # Given / When
    result = discord_link(space=case.space, channel_id=case.channel,
                          message_id=case.message, guild_id=case.guild)
    # Then
    assert result.status is LinkStatus.AVAILABLE
    assert result.url == f"https://discord.com/channels/{path}"
    assert result.detail is None


@pytest.mark.parametrize("case", [LinkCase("guild", None, guild="111"),
    LinkCase("guild", "222"), LinkCase("dm", None), LinkCase("unknown", "222", "333", "111")])
def test_unavailable_when_required_coordinates_are_missing_or_space_is_unknown(case: LinkCase) -> None:
    # Given / When
    result = discord_link(space=case.space, channel_id=case.channel,
                          message_id=case.message, guild_id=case.guild)
    # Then
    assert result.status is LinkStatus.UNAVAILABLE
    assert result.url is None and result.detail


@pytest.mark.parametrize("value", ["", " ", "\t\n", "0", "00", "-1", "+1", "1.0",
                                   "２", "١", "bad", "1/2", "1?x", "1#x", "1\n"])
def test_invalid_when_any_present_coordinate_is_not_a_positive_ascii_decimal(value: str) -> None:
    # Given
    coordinates = ((value, "333", "111"), ("222", value, "111"), ("222", "333", value))
    # When
    results = [discord_link(space="guild", channel_id=channel, message_id=message, guild_id=guild)
               for channel, message, guild in coordinates]
    # Then
    assert all(result.status is LinkStatus.INVALID and result.url is None and result.detail
               for result in results)


def test_long_decimal_when_coordinate_exceeds_python_integer_conversion_limit() -> None:
    # Given
    value = "1" * 5000
    # When
    result = discord_link(space="dm", channel_id=value)
    # Then
    assert result.status is LinkStatus.AVAILABLE
    assert result.url == f"https://discord.com/channels/@me/{value}"


def test_total_link_boundary_when_coordinate_fields_vary_independently() -> None:
    # Given: Cartesian product includes missing plus malformed sibling fields.
    spaces: tuple[Space, ...] = ("guild", "dm", "unknown")
    values: Final = (None, "", " \t", "bad", "0", "２", "222")
    for space, channel, message, guild in product(spaces, values, values, values):
        # When
        result = discord_link(space=space, channel_id=channel, message_id=message, guild_id=guild)
        # Then
        assert result.status in (LinkStatus.AVAILABLE, LinkStatus.UNAVAILABLE, LinkStatus.INVALID)
        if result.url is not None:
            assert result.url in {"https://discord.com/channels/222/222",
                                  "https://discord.com/channels/222/222/222",
                                  "https://discord.com/channels/@me/222",
                                  "https://discord.com/channels/@me/222/222"}
        else:
            assert result.detail


@pytest.mark.parametrize("scope", ["none", "self", "channel", "message"])
def test_search_is_omitted_when_scope_is_local(
    scope: Literal["none", "self", "channel", "message"],
) -> None:
    # Given: none/self ignore search; Discord locations share a known thread.
    location = Ref(scope, "guild", "111", "222", "333", search=("L42", "K42"))
    message = replace(_MESSAGE, location=location)
    destination = Ref("channel", channel_id="222")
    # When
    body = render(message, destination=destination)
    # Then: preserve local output without pinning Korean copy.
    value = body.splitlines()[2].partition(": ")[2]
    assert value and "http" not in value
    assert "L42" not in value and "K42" not in value


@pytest.mark.parametrize("location", [
    Ref("message", search=("L42", "K42")),
    Ref("resource", search=("L42", "K42")),
])
def test_search_is_preserved_when_destination_has_a_different_channel(location: Ref) -> None:
    # Given: characterize both fallbacks before correcting missing-id locality.
    message = replace(_MESSAGE, location=location)
    destination = Ref("channel", channel_id="999")
    # When
    body = render(message, destination=destination)
    # Then
    value = body.splitlines()[2].partition(": ")[2]
    assert "L42" in value and "K42" in value
    assert "http" not in value


@pytest.mark.parametrize("scope", ["channel", "message", "resource"])
def test_search_is_preserved_when_both_channel_coordinates_are_missing(
    scope: Literal["channel", "message", "resource"],
) -> None:
    # Given: a missing coordinate is not evidence of locality.
    location = Ref(scope, search=("L42", "K42"))
    message = replace(_MESSAGE, location=location)
    # When
    body = render(message, destination=Ref("none"))
    # Then: inspect the location, not the unrelated subject key.
    value = body.splitlines()[2].partition(": ")[2]
    assert "L42" in value and "K42" in value
    assert "http" not in value and "@me" not in value


@pytest.mark.parametrize("channel", [None, "222"])
def test_resource_url_is_preserved_when_channel_coordinates_match(channel: str | None) -> None:
    # Given: a resource remains external even with matching channel metadata.
    url = "https://drive.google.com/file/d/x"
    location = Ref("resource", channel_id=channel, url=url)
    message = replace(_MESSAGE, location=location, owner=Action("open", location))
    destination = Ref("none") if channel is None else Ref("channel", channel_id=channel)
    # When
    body = render(message, destination=destination)
    # Then: the action reuses the location rather than duplicating the URL.
    assert body.count(url) == 1
    assert body.splitlines()[2].partition(": ")[2] == url


@pytest.mark.parametrize("action_field", ["owner", "recovery"])
def test_action_search_is_preserved_when_target_channel_coordinates_are_missing(
    action_field: Literal["owner", "recovery"],
) -> None:
    # Given: action targets use the same reference seam as the location.
    target = Ref("message", search=("L42", "K42"))
    message = replace(_MESSAGE, **{action_field: Action("open", target)})
    # When
    body = render(message, destination=Ref("none"))
    # Then
    assert "L42" in body and "K42" in body
