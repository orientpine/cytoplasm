"""Optional guild-cache failures never prevent mandatory approval binding."""
from __future__ import annotations

import json
from contextlib import nullcontext
from email.message import Message
from pathlib import Path
from typing import Literal, assert_never
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.approval_surface import ApprovalKind, RequestThread, resolve_new_binding
from tests.unit import test_approval_guild_directory as directory_tests
from tests.unit.test_approval_directory import FakeApi, _fingerprint

configured = directory_tests.configured
CacheFault = Literal["malformed", "truncated", "object-type", "guild-type", "unreadable", "write", "parent-chmod", "file-chmod"]
_READ_FAULTS: tuple[CacheFault, ...] = ("malformed", "truncated", "object-type", "guild-type", "unreadable")


@pytest.mark.parametrize("reuse,fault", [(reuse, fault) for reuse in (False, True) for fault in _READ_FAULTS] + [(False, fault) for fault in ("write", "parent-chmod", "file-chmod")])
def test_binding_proceeds_with_unknown_guild_when_cache_fails(configured: Path, reuse: bool, fault: CacheFault) -> None:
    # Given: valid Discord surface facts, but optional metadata storage is broken.
    configured.parent.mkdir()
    match fault:
        case "malformed":
            configured.write_text("not-json", encoding="utf-8")
        case "truncated":
            configured.write_text('{"token_fingerprint":', encoding="utf-8")
        case "object-type":
            configured.write_text("[]", encoding="utf-8")
        case "guild-type":
            configured.write_text(json.dumps({"token_fingerprint": _fingerprint("identity-a"), "approval_guild_id": 111}), encoding="utf-8")
        case "unreadable":
            configured.mkdir()
        case "write" | "parent-chmod" | "file-chmod":
            pass  # The refusal is injected only around the binding action below.
        case unreachable:
            assert_never(unreachable)
    path = "/channels/222/messages/444/threads" if reuse else "/channels/222/messages/666/threads"
    api = FakeApi("identity-a", {
        ("POST", "/channels/222/messages"): {"id": "666"},
        ("POST", path): HTTPError("https://discord.test", 400, "exists", Message(), None) if reuse else {"id": "444", "guild_id": "111"},
        ("GET", "/channels/444"): {"type": 11, "name": "request", "parent_id": "222", "guild_id": "999"},
    })
    directory = DiscordChannelDirectory("identity-a", "333", api, configured)
    request = RequestThread("request", "222", "444") if reuse else RequestThread("request")
    permission_error = PermissionError("injected optional cache refusal")
    match fault:
        case "write":
            refusal = patch.object(Path, "write_text", side_effect=permission_error)
        case "parent-chmod":
            refusal = patch.object(Path, "chmod", side_effect=permission_error)
        case "file-chmod":
            refusal = patch.object(Path, "chmod", side_effect=[None, permission_error])
        case "malformed" | "truncated" | "object-type" | "guild-type" | "unreadable":
            refusal = nullcontext()
        case unreachable:
            assert_never(unreachable)
    # When: request binding uses the actual directory and surface validator.
    with refusal:
        binding = resolve_new_binding(ApprovalKind.TODO, directory, "333", request=request)
    # Then: the approval remains usable, without adopting validation GET metadata.
    assert (binding.channel_id, binding.guild_id) == ("444", None)
    assert [(method, endpoint) for method, endpoint, _ in api.calls] == (
        ([] if reuse else [("POST", "/channels/222/messages")])
        + [("POST", path), ("GET", "/channels/444")]
    )


@pytest.mark.parametrize("cached", [False, True])
def test_400_guild_comes_only_from_cache_when_validation_get_disagrees(configured: Path, cached: bool) -> None:
    # Given: validation GET disagrees with the only permitted guild source.
    if cached:
        configured.parent.mkdir()
        configured.write_text(json.dumps({"token_fingerprint": _fingerprint("identity-a"), "approval_guild_id": "111"}), encoding="utf-8")
    path = "/channels/222/messages/444/threads"
    api = FakeApi("identity-a", {
        ("POST", path): HTTPError("https://discord.test", 400, "exists", Message(), None),
        ("GET", "/channels/444"): {"type": 11, "name": "request", "parent_id": "222", "guild_id": "999"},
    })
    # When: Discord reports an existing anchored thread without a response body.
    binding = resolve_new_binding(ApprovalKind.TODO, DiscordChannelDirectory("identity-a", "333", api, configured), "333", request=RequestThread("request", "222", "444"))
    # Then: cached metadata or unknown wins, never the validation GET.
    assert binding.guild_id == ("111" if cached else None)
    assert len(api.calls) == 2


def test_cache_parent_becomes_private_when_existing_directory_is_wide(configured: Path) -> None:
    # Given: a pre-existing directory whose mode mkdir(exist_ok=True) cannot repair.
    configured.parent.mkdir()
    configured.parent.chmod(0o755)
    api = FakeApi("identity-a", {
        ("POST", "/channels/222/messages"): {"id": "666"},
        ("POST", "/channels/222/messages/666/threads"): {"id": "444", "guild_id": "111"},
        ("GET", "/channels/444"): {"type": 11, "name": "request", "parent_id": "222"},
    })
    # When: creation persists guild metadata in the existing cache location.
    binding = resolve_new_binding(ApprovalKind.TODO, DiscordChannelDirectory("identity-a", "333", api, configured), "333", request=RequestThread("request"))
    # Then: both levels of the cache are private and metadata is preserved.
    assert (configured.parent.stat().st_mode & 0o777, configured.stat().st_mode & 0o777) == (0o700, 0o600)
    assert binding.guild_id == "111"
