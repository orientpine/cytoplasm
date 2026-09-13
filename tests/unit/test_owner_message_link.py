"""Reminder-side deep-link contract; shared builder coverage lives in links (plural)."""
from __future__ import annotations

import pytest

from automation.interop import owner_message
from automation.interop.approval_reminder import (
    DiscordSource,
    LinkResult,
    LinkStatus,
    SourceLifecycle,
    SourceReference,
    discord_message_link,
    resolve_source_link,
)


def test_deleted_status_wins_when_coordinates_are_malformed() -> None:
    # Given a lifecycle lookup that already reported deletion.
    source = SourceReference(
        platform="discord", discord=DiscordSource("bad", "333", "111"),
        lifecycle=SourceLifecycle.DELETED,
    )
    # When the reminder resolves its source.
    result = resolve_source_link(source)
    # Then deletion survives even though coordinates cannot form a link.
    assert result.status is LinkStatus.DELETED
    assert result.url is None


def test_link_is_unavailable_when_guild_and_space_are_unknown() -> None:
    # Given a thread whose guild could not be resolved.
    source = DiscordSource(channel_id="222", message_id="333")
    # When the reminder asks for its direct link.
    result = discord_message_link(source)
    # Then missing evidence is never silently promoted to a DM route.
    assert result.status is LinkStatus.UNAVAILABLE
    assert result.url is None
    assert result.detail


def test_guild_link_is_preserved_when_space_is_omitted() -> None:
    # Given a known guild, not a guessed DM.
    source = DiscordSource("222", "333", "111")
    # When the legacy public wrapper resolves the source.
    result = discord_message_link(source)
    # Then its canonical guild coordinates remain available.
    assert result.status is LinkStatus.AVAILABLE
    assert result.url == "https://discord.com/channels/111/222/333"


@pytest.mark.parametrize("space", ["guild", "dm"])
@pytest.mark.parametrize("message_id", [None, "333"])
def test_url_shape_when_space_is_explicit(space: owner_message.Space, message_id: str | None) -> None:
    # Given channel/thread or message coordinates with an explicit space.
    source = DiscordSource("222", message_id, "111", space=space)
    # When the reminder resolves the source through its public API.
    result = resolve_source_link(SourceReference(platform="discord", discord=source))
    # Then all four supported URL shapes point at the requested coordinates.
    root = {"guild": "111", "dm": "@me"}[space]
    suffix = "" if message_id is None else "/333"
    assert result.status is LinkStatus.AVAILABLE
    assert result.url == f"https://discord.com/channels/{root}/222{suffix}"


@pytest.mark.parametrize("guild_id", [None, "111"])
def test_explicit_unknown_wins_when_guild_coordinates_are_present(guild_id: str | None) -> None:
    # Given an explicit unknown classification, even with stale guild metadata.
    source = DiscordSource("222", "333", guild_id, space="unknown")
    # When the reminder creates its link.
    result = discord_message_link(source)
    # Then the explicit classification prevents an invented destination.
    assert result.status is LinkStatus.UNAVAILABLE
    assert result.url is None
    assert result.detail


def test_guild_link_is_unavailable_when_guild_coordinate_is_missing() -> None:
    # Given a known guild surface without the guild coordinate.
    source = DiscordSource("222", "333", space="guild")
    # When the wrapper delegates the incomplete coordinates.
    result = discord_message_link(source)
    # Then it cannot become a DM URL.
    assert result.status is LinkStatus.UNAVAILABLE
    assert result.url is None
    assert result.detail


@pytest.mark.parametrize("coordinates", [("bad", "333", "111"), ("222", "bad", "111"),
                                          ("222", "333", "bad"), ("0", "333", "111"),
                                          ("２２２", "333", "111"), ("222", "", "111")])
def test_link_is_invalid_when_coordinates_are_not_snowflakes(coordinates: tuple[str, str, str]) -> None:
    # Given malformed persisted coordinates, not an unknown space.
    channel_id, message_id, guild_id = coordinates
    source = DiscordSource(channel_id, message_id, guild_id, space="guild")
    # When the wrapper delegates validation to the single builder.
    result = discord_message_link(source)
    # Then no malformed URL escapes.
    assert result.status is LinkStatus.INVALID
    assert result.url is None


@pytest.mark.parametrize("space", ["elsewhere", "", "DM"])
def test_link_is_invalid_when_runtime_space_is_unsupported(space: owner_message.Space) -> None:
    # Given a runtime caller violating the annotated space contract.
    source = DiscordSource("222", "333", space=space)
    # When the boundary receives the malformed variant.
    result = discord_message_link(source)
    # Then rejection is a result, not an exception or a guessed URL.
    assert result.status is LinkStatus.INVALID
    assert result.url is None


def test_public_types_are_shared_when_consumers_import_legacy_names() -> None:
    # Given the legacy reminder imports and the owner-message contract.
    # When their public types are compared.
    # Then result identity and enum identity survive the migration.
    assert LinkResult is owner_message.LinkResult
    assert LinkStatus is owner_message.LinkStatus
