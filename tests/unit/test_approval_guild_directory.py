"""Guild coordinates are metadata, never another Discord lookup."""
from __future__ import annotations

import json
from dataclasses import replace
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.approval_surface import (
    POLICY_VERSION, ApprovalBinding, ApprovalKind, ApprovalSurface, ApprovalSurfaceError, RequestThread,
    resolve_new_binding, reuse_request_thread, validate_stored_binding,
)
from tests.unit.test_approval_directory import FakeApi, _fingerprint
from tests.unit.test_plaud_sync_model import _BASE


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    config = tmp_path / "interop.json"
    config.write_text(json.dumps({"agent_chat_channel_id": "222"}), encoding="utf-8")
    monkeypatch.setenv("INTEROP_CONFIG", str(config))
    return tmp_path / "private" / "approval-directory.json"


def test_request_thread_creation_keeps_guild_without_extra_get(configured: Path) -> None:
    # Given: the create response already contains the guild coordinate.
    api = FakeApi("identity-a", {
        ("POST", "/channels/222/messages"): {"id": "555"},
        ("POST", "/channels/222/messages/555/threads"): {"id": "444", "guild_id": "111"},
    })
    directory = DiscordChannelDirectory("identity-a", "333", api, configured)
    # When: a request opens its own thread.
    thread = directory.agent_chat_request_thread(ApprovalKind.TODO, RequestThread("request"))
    # Then: main's announcement anchors the thread; guild preservation adds no GET.
    assert thread == "444"
    assert directory._approval_guild_id == "111"
    assert [(method, path) for method, path, _ in api.calls] == [
        ("POST", "/channels/222/messages"), ("POST", "/channels/222/messages/555/threads"),
    ]


def test_request_thread_reuse_keeps_one_post_when_message_already_has_thread(configured: Path) -> None:
    # Given: Discord's existing-thread response has no channel body.
    path = "/channels/222/messages/444/threads"
    api = FakeApi("identity-a", {("POST", path): HTTPError("https://discord.test", 400, "exists", Message(), None)})
    directory = DiscordChannelDirectory("identity-a", "333", api, configured)
    # When: the instruction message anchors this approval.
    thread = directory.agent_chat_request_thread(ApprovalKind.TODO, RequestThread("request", "222", "444"))
    # Then: the message remains the thread, with no fallback GET.
    assert thread == "444"
    assert len(api.calls) == 1


def test_stored_binding_is_unchanged_when_description_has_guild(configured: Path) -> None:
    # Given: a legacy binding lacking guild metadata.
    api = FakeApi("identity-a", {("GET", "/channels/444"): {
        "type": 11, "name": "request", "parent_id": "222", "guild_id": "111",
    }})
    directory = DiscordChannelDirectory("identity-a", "333", api, configured)
    binding = ApprovalBinding(ApprovalKind.TODO, ApprovalSurface.AGENT_CHAT_THREAD, "444", POLICY_VERSION)
    # When: stored surface facts are checked.
    validated = validate_stored_binding(binding, directory, "333")
    # Then: validation is not a metadata migration.
    assert validated is binding


def test_guild_is_bound_when_thread_create_response_contains_it(configured: Path) -> None:
    # Given: only the POST contains guild_id; validation still uses its existing GET.
    api = FakeApi("identity-a", {
        ("POST", "/channels/222/messages"): {"id": "666"},
        ("POST", "/channels/222/messages/666/threads"): {"id": "444", "guild_id": "111"},
        ("GET", "/channels/444"): {"type": 11, "name": "request", "parent_id": "222"},
    })
    directory = DiscordChannelDirectory("identity-a", "333", api, configured)
    # When: a new binding resolves this request.
    binding = resolve_new_binding(ApprovalKind.TODO, directory, "333", request=RequestThread("request"))
    # Then: the guild survives both binding and the private account cache.
    assert getattr(binding, "guild_id", None) == "111"
    assert json.loads(configured.read_text())["approval_guild_id"] == "111"
    assert configured.stat().st_mode & 0o777 == 0o600
    assert configured.parent.stat().st_mode & 0o777 == 0o700
    assert [(method, path) for method, path, _ in api.calls] == [
        ("POST", "/channels/222/messages"), ("POST", "/channels/222/messages/666/threads"),
        ("GET", "/channels/444"),
    ]


@pytest.mark.parametrize("guild", [None, 111, "invalid", "", "@me"])
def test_guild_stays_unknown_when_create_metadata_is_malformed(configured: Path, guild: str | int | None) -> None:
    # Given: malformed optional metadata, not malformed surface facts.
    api = FakeApi("identity-a", {
        ("POST", "/channels/222/messages"): {"id": "666"},
        ("POST", "/channels/222/messages/666/threads"): {"id": "444", "guild_id": guild},
        ("GET", "/channels/444"): {"type": 11, "name": "request", "parent_id": "222"},
    })
    # When: the approval is bound.
    binding = resolve_new_binding(ApprovalKind.TODO, DiscordChannelDirectory("identity-a", "333", api, configured), "333", request=RequestThread("request"))
    # Then: optional metadata never breaks an approval or guesses a DM coordinate.
    assert getattr(binding, "guild_id", None) is None


@pytest.mark.parametrize("token,expected", [("identity-a", "111"), ("identity-b", None)])
def test_guild_cache_is_account_bound_when_reusing_400_thread(configured: Path, token: str, expected: str | None) -> None:
    # Given: one identity cached a guild from a previous thread creation.
    seed = FakeApi("identity-a", {("POST", "/channels/222/messages"): {"id": "666"},
        ("POST", "/channels/222/messages/666/threads"): {"id": "555", "guild_id": "111"}})
    DiscordChannelDirectory("identity-a", "333", seed, configured).agent_chat_request_thread(ApprovalKind.TODO, RequestThread("seed"))
    path = "/channels/222/messages/444/threads"
    api = FakeApi(token, {
        ("POST", path): HTTPError("https://discord.test", 400, "exists", Message(), None),
        ("GET", "/channels/444"): {"type": 11, "name": "request", "parent_id": "222"},
    })
    # When: a new directory instance reuses the anchored thread.
    binding = resolve_new_binding(ApprovalKind.TODO, DiscordChannelDirectory(token, "333", api, configured), "333", request=RequestThread("request", "222", "444"))
    # Then: only the same account may recover the coordinate, without a parent GET.
    assert getattr(binding, "guild_id", None) == expected
    assert len(api.calls) == 2


def test_guild_is_preserved_when_reusing_prior_record_without_cached_metadata(configured: Path) -> None:
    # Given: a durable guild coordinate and no account cache or API guild field.
    record = replace(_BASE, channel_id="444", approval_guild_id="111")
    api = FakeApi("identity-a", {("GET", "/channels/444"): {"type": 11, "name": "request", "parent_id": "222"}})
    # When: this request reuses its own live thread.
    binding = reuse_request_thread(ApprovalKind.TODO, (record,), DiscordChannelDirectory("identity-a", "333", api, configured), "333")
    # Then: reuse does not discard durable coordinates.
    assert binding is not None
    assert binding.guild_id == "111"


def test_guild_is_unknown_when_description_is_a_dm(configured: Path) -> None:
    # Given: a valid DM response has no guild_id.
    api = FakeApi("identity-a", {("GET", "/channels/444"): {"type": 1, "recipients": [{"id": "333"}]}})
    # When: existing channel facts are parsed.
    facts = DiscordChannelDirectory("identity-a", "333", api, configured).describe("444")
    # Then: the missing guild is allowed, not guessed.
    assert facts.guild_id is None
    assert facts.channel_type == 1


def test_guild_cache_preserves_approvals_when_both_coordinates_are_resolved(configured: Path) -> None:
    # Given: one identity discovers the existing skill channel first.
    api = FakeApi("identity-a", {
        ("GET", "/users/@me/guilds"): [{"id": "111"}],
        ("GET", "/guilds/111/channels"): [{"id": "555", "type": 0, "name": "approvals"}],
        ("POST", "/channels/222/messages"): {"id": "666"},
        ("POST", "/channels/222/messages/666/threads"): {"id": "444", "guild_id": "111"},
    })
    directory = DiscordChannelDirectory("identity-a", "333", api, configured)
    assert directory.skill_approvals() == "555"
    # When: request creation writes the second cache coordinate.
    directory.agent_chat_request_thread(ApprovalKind.TODO, RequestThread("request"))
    # Then: both fields coexist under the same private fingerprint.
    cached = json.loads(configured.read_text())
    assert (cached["approvals_channel_id"], cached["approval_guild_id"]) == ("555", "111")


def test_approvals_cache_still_refuses_when_matching_entry_has_no_coordinates(configured: Path) -> None:
    # Given: an existing malformed cache, not the newly supported guild-only shape.
    configured.parent.mkdir()
    configured.write_text(json.dumps({"token_fingerprint": _fingerprint("identity-a")}))
    api = FakeApi("identity-a", {})
    # When / Then: adding guild metadata must not turn this fail-closed case into a scan.
    with pytest.raises(ApprovalSurfaceError):
        DiscordChannelDirectory("identity-a", "333", api, configured).skill_approvals()
    assert api.calls == []


def test_approvals_scan_keeps_guild_when_cache_contains_only_guild(configured: Path) -> None:
    # Given: a request thread populated this identity's cache before a skill scan.
    api = FakeApi("identity-a", {
        ("POST", "/channels/222/messages"): {"id": "666"},
        ("POST", "/channels/222/messages/666/threads"): {"id": "444", "guild_id": "111"},
        ("GET", "/users/@me/guilds"): [{"id": "111"}],
        ("GET", "/guilds/111/channels"): [{"id": "555", "type": 0, "name": "approvals"}],
    })
    directory = DiscordChannelDirectory("identity-a", "333", api, configured)
    directory.agent_chat_request_thread(ApprovalKind.TODO, RequestThread("request"))
    # When: the other cache coordinate is discovered.
    channel_id = directory.skill_approvals()
    # Then: the existing guild-only cache neither blocks scanning nor loses its guild.
    assert channel_id == "555"
    assert json.loads(configured.read_text())["approval_guild_id"] == "111"
