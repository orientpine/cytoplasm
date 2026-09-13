"""Stored reminder coordinates through real caller ticks with injected transports."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeAlias

import pytest

from tests.unit.approval_reminder_callers import ROOT, Case, Runtime, Setup


RuntimeFactory: TypeAlias = Callable[[Case], Runtime]


@pytest.fixture(params=("mail", "calendar", "todo"))
def runtime(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RuntimeFactory:
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(ROOT))

    def prepare(case: Case) -> Runtime:
        setup = Setup(tmp_path, monkeypatch, case)
        return {"mail": setup.mail, "calendar": setup.calendar, "todo": setup.todo}[request.param]()
    return prepare


def test_reminder_delivers_without_guessed_link_when_record_is_legacy(runtime: RuntimeFactory) -> None:
    # Given: a stored thread binding without a guild coordinate.
    prepared = runtime(Case())
    # When: the caller runs a due tick through the real lifecycle and journal.
    prepared.tick()
    # Then: delivery survives, without inventing a direct-message link.
    [(destination, body)] = prepared.delivery.posts
    assert destination in ("222", "owner-callback")
    assert "@me" not in body and "discord.com" not in body
    print(f"legacy {destination}: {body}")


def test_reminder_keeps_delivery_disabled_when_configuration_is_disabled(runtime: RuntimeFactory) -> None:
    # Given: a stored pending approval and disabled reminders.
    prepared = runtime(Case(enabled=False))
    # When: the real caller ticks.
    prepared.tick()
    # Then: no transport delivery occurs.
    assert prepared.delivery.posts == []


def test_reminder_links_stored_guild_when_record_has_coordinates(runtime: RuntimeFactory) -> None:
    # Given: a record guild different from the directory's guild (999).
    prepared = runtime(Case(guild_id="111"))
    # When: the real caller renders and delivers a due reminder.
    prepared.tick()
    # Then: exactly one stored-card URL survives the existing delivery callback.
    [(destination, body)] = prepared.delivery.posts
    assert destination in ("222", "owner-callback")
    assert body.count("https://discord.com/channels/111/222/333") == 1
    assert body.count("https://") == 1
    print(f"guild {destination}: {body}")


def test_reminder_links_dm_when_stored_binding_explicitly_names_dm(runtime: RuntimeFactory) -> None:
    # Given: an explicitly stamped historical direct-message binding, not an unknown guild.
    prepared = runtime(Case(dm=True))
    # When: the real caller ticks without changing its surface decisions.
    prepared.tick()
    # Then: the stored DM signal permits the correct card URL.
    [(_, body)] = prepared.delivery.posts
    assert body.count("https://discord.com/channels/@me/222/333") == 1


def test_reminder_delivers_without_link_when_guild_is_empty(runtime: RuntimeFactory) -> None:
    # Given: empty optional metadata on an otherwise valid stored binding.
    prepared = runtime(Case(guild_id=""))
    # When: the real caller ticks.
    prepared.tick()
    # Then: malformed optional metadata does not black out delivery.
    [(_, body)] = prepared.delivery.posts
    assert "@me" not in body and "discord.com" not in body


def test_reminder_delivers_without_link_when_raw_guild_is_non_string(runtime: RuntimeFactory) -> None:
    # Given: malformed optional metadata (injected after the typed todo parser).
    prepared = runtime(Case(guild_id=111))
    # When: the caller passes the record through its reminder boundary.
    prepared.tick()
    # Then: no guessed URL is emitted and delivery still succeeds.
    [(_, body)] = prepared.delivery.posts
    assert "@me" not in body and "discord.com" not in body
