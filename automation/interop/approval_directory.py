"""The sole approval-path Discord ``ChannelDirectory`` resolver.

AS-1.3 exempts only this module from the approval-path resolver guard: it may
open the owner DM, read the approval-channel config keys, consult the cache,
scan guilds, and (v7) find-or-create the per-kind approval threads under the
``agent_chat_channel_id`` channel. AS-3.2 retired the per-flow ``*_APPROVALS_CHANNEL_ID`` compatibility
branch, so an approval surface is now resolved from the config key, the cache or a
guild scan and from nothing else — no caller can name an environment variable to
point one somewhere. The exemption is intentionally narrower than the whole
repository: six non-approval DM senders remain outside this directory —
``procure_review.send_review``, ``cost-report.send_cost_report``,
``interop.gate_driver.main``, ``interop.hermes_plugin._send_direct_result``,
``reminder_poller.DmSender.send``, and ``research_trends._send_dm``.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol, TypeAlias
from urllib.request import Request, urlopen

from automation.interop.approval_surface import ApprovalKind, ApprovalSurfaceError, ChannelFacts

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

_DISCORD_API: Final = "https://discord.com/api/v10"
_INTEROP_CONFIG: Final = "~/.hermes/interop/config.json"
_OWNER_DM_PATH: Final = "/users/@me/channels"
_USER_AGENT: Final = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
_CACHE_FINGERPRINT_LENGTH: Final = 16


class DiscordApi(Protocol):
    def __call__(
        self,
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> JsonValue: ...


def _unbound_api(
    method: str,
    path: str,
    payload: dict[str, JsonValue] | None = None,
) -> JsonValue:
    del method, path, payload
    raise ApprovalSurfaceError("Discord API was not bound")


def _stdlib_api(token: str) -> DiscordApi:
    def call(
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> JsonValue:
        request = Request(
            f"{_DISCORD_API}{path}",
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
            method=method,
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 - Discord HTTPS endpoint
            body = response.read().decode("utf-8")
        return json.loads(body) if body else None

    return call


@dataclass(frozen=True, slots=True)
class DiscordChannelDirectory:
    """Resolve concrete, verified-later Discord channel ids for one bot identity."""

    token: str | None
    owner_id: str
    api: DiscordApi = field(default=_unbound_api, repr=False, compare=False)
    cache_path: Path | None = None
    _owner_dm_channel_id: str | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.owner_id:
            raise ApprovalSurfaceError("Discord directory requires an owner id")
        if self.api is _unbound_api:
            if not self.token:
                raise ApprovalSurfaceError("Discord directory requires a bot token or injected API")
            object.__setattr__(self, "api", _stdlib_api(self.token))

    def owner_dm(self) -> str:
        """Open and memoise this bot's owner DM without writing it to disk."""
        if self._owner_dm_channel_id is not None:
            return self._owner_dm_channel_id
        response = self._request("POST", _OWNER_DM_PATH, {"recipient_id": self.owner_id})
        channel_id = _required_string(_json_object(response, "owner DM"), "id", "owner DM")
        object.__setattr__(self, "_owner_dm_channel_id", channel_id)
        return channel_id

    def skill_approvals(self) -> str:
        """Resolve the configured, cached, or uniquely scanned skill approval channel."""
        configured = _configured_approvals_channel()
        if configured is not None:
            return configured
        cached = self._cached_approvals_channel()
        if cached is not None:
            return cached
        channel_id = self._scan_for_approvals()
        self._write_cache(channel_id)
        return channel_id

    def agent_chat(self) -> str:
        """The configured owner-agent chat channel — config key only, fail closed."""
        configured = _configured_agent_chat_channel()
        if configured is None:
            raise ApprovalSurfaceError(
                "agent_chat_channel_id is not configured in the interop config",
            )
        return configured

    def agent_chat_thread(self, kind: ApprovalKind) -> str:
        """Find or create this kind's approval thread under the agent-chat channel.

        Reuse order is deterministic: an active thread, then a public archived one
        (posting into it un-archives it), then a fresh channel thread with a 7-day
        auto-archive window. Matching is by parent channel, frozen name, and thread
        type — a same-name thread under another channel never matches.
        """
        channel_id = self.agent_chat()
        name = f"승인-{kind.value}"
        guild_id = _required_string(
            _json_object(self._request("GET", f"/channels/{channel_id}"), "agent-chat channel"),
            "guild_id",
            "agent-chat channel",
        )
        active = _matching_thread(
            self._request("GET", f"/guilds/{guild_id}/threads/active"), channel_id, name,
        )
        if active is not None:
            return active
        archived = _matching_thread(
            self._request("GET", f"/channels/{channel_id}/threads/archived/public"),
            channel_id,
            name,
        )
        if archived is not None:
            return archived
        created = self._request(
            "POST",
            f"/channels/{channel_id}/threads",
            {"name": name, "auto_archive_duration": 10080, "type": 11},
        )
        return _required_string(
            _json_object(created, "agent-chat thread"), "id", "agent-chat thread",
        )

    def describe(self, channel_id: str) -> ChannelFacts:
        """Return parsed channel facts or refuse an unverifiable Discord response."""
        body = _json_object(self._request("GET", f"/channels/{channel_id}"), "channel description")
        channel_type = body.get("type")
        if isinstance(channel_type, bool) or not isinstance(channel_type, int):
            raise ApprovalSurfaceError("channel description omitted an integer type")
        name = body.get("name")
        if name is None:
            channel_name = ""
        elif isinstance(name, str):
            channel_name = name
        else:
            raise ApprovalSurfaceError("channel description has an invalid name")
        recipients = body.get("recipients", [])
        if not isinstance(recipients, list):
            raise ApprovalSurfaceError("channel description has invalid recipients")
        recipient_ids = tuple(
            _required_string(_json_object(recipient, "channel recipient"), "id", "channel recipient")
            for recipient in recipients
        )
        parent = body.get("parent_id")
        if parent is not None and not isinstance(parent, str):
            raise ApprovalSurfaceError("channel description has an invalid parent")
        return ChannelFacts(channel_type, channel_name, recipient_ids, parent)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> JsonValue:
        try:
            return self.api(method, path, payload)
        except ApprovalSurfaceError:
            raise
        except Exception as error:  # noqa: BLE001 - injected REST boundary must fail closed
            # Redaction contract: `api` is an injected DiscordApi callable (e.g. skills/mail's
            # triage_binding passes triage_confirm._api), so its exception text is untrusted
            # and may embed a request url or a credential.
            # Only the cause TYPE and the integer HTTP status may cross this boundary — never
            # str/repr(error), .url, .reason, .headers or any response body.
            name = type(error).__name__
            status = ""
            if isinstance(error, urllib.error.HTTPError):
                status = f" http_status={error.code}"
            raise ApprovalSurfaceError(
                f"Discord request failed: {method} {path} cause={name}{status}",
            ) from error

    def _cached_approvals_channel(self) -> str | None:
        if self.cache_path is None or self.token is None:
            return None
        try:
            text = self.cache_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ApprovalSurfaceError(f"approval cache is unreadable: {self.cache_path}") from error
        try:
            cache = _json_object(json.loads(text), "approval cache")
        except json.JSONDecodeError as error:
            raise ApprovalSurfaceError(f"approval cache is malformed: {self.cache_path}") from error
        fingerprint = cache.get("token_fingerprint")
        if fingerprint != self._token_fingerprint():
            return None
        return _required_string(cache, "approvals_channel_id", "approval cache")

    def _scan_for_approvals(self) -> str:
        guilds = self._request("GET", "/users/@me/guilds")
        if not isinstance(guilds, list):
            raise ApprovalSurfaceError("guild list response is malformed")
        matches: list[str] = []
        for guild in guilds:
            guild_id = _required_string(_json_object(guild, "guild"), "id", "guild")
            channels = self._request("GET", f"/guilds/{guild_id}/channels")
            if not isinstance(channels, list):
                raise ApprovalSurfaceError("guild channel list response is malformed")
            for channel in channels:
                details = _json_object(channel, "guild channel")
                if details.get("type") == 0 and details.get("name") == "approvals":
                    matches.append(_required_string(details, "id", "approvals channel"))
        if len(matches) != 1:
            raise ApprovalSurfaceError("approvals channel is absent or ambiguous across guilds")
        return matches[0]

    def _write_cache(self, channel_id: str) -> None:
        if self.cache_path is None or self.token is None:
            return
        payload = {
            "token_fingerprint": self._token_fingerprint(),
            "approvals_channel_id": channel_id,
        }
        try:
            self.cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            self.cache_path.chmod(0o600)
        except OSError as error:
            raise ApprovalSurfaceError(f"approval cache cannot be written: {self.cache_path}") from error

    def _token_fingerprint(self) -> str:
        if self.token is None:
            raise ApprovalSurfaceError("tokenless injected API has no cache fingerprint")
        return hashlib.sha256(self.token.encode("utf-8")).hexdigest()[:_CACHE_FINGERPRINT_LENGTH]


def _configured_approvals_channel() -> str | None:
    return _interop_config_string("personal_approvals_channel_id")


def _configured_agent_chat_channel() -> str | None:
    return _interop_config_string("agent_chat_channel_id")


def _interop_config_string(key: str) -> str | None:
    path = Path(os.environ.get("INTEROP_CONFIG", _INTEROP_CONFIG)).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ApprovalSurfaceError(f"interop config is unreadable: {path}") from error
    try:
        config = _json_object(json.loads(text), "interop config")
    except json.JSONDecodeError as error:
        raise ApprovalSurfaceError(f"interop config is malformed: {path}") from error
    value = config.get(key)
    if value is None:
        return None
    return _required_string(config, key, "interop config")


def _matching_thread(listing: JsonValue, channel_id: str, name: str) -> str | None:
    body = _json_object(listing, "thread listing")
    threads = body.get("threads", [])
    if not isinstance(threads, list):
        raise ApprovalSurfaceError("thread listing response is malformed")
    for thread in threads:
        details = _json_object(thread, "thread")
        if (
            details.get("parent_id") == channel_id
            and details.get("name") == name
            and details.get("type") in (11, 12)
        ):
            return _required_string(details, "id", "thread")
    return None


def _json_object(value: JsonValue, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ApprovalSurfaceError(f"{context} response is not an object")
    return value


def _required_string(payload: dict[str, JsonValue], key: str, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ApprovalSurfaceError(f"{context} omitted {key}")
    return value
