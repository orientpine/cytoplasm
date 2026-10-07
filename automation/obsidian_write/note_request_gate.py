"""Shared-lifecycle approval adapter and Discord runtime for note requests.

Mirrors ``memory_relocate.approval_gate``: the façade owns lease → probe → supersede →
post → commit, this adapter only maps it onto ``NoteRequestStore`` and the shared
reaction transport. Only an owner (non-bot) reaction on the bound card counts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Final
from urllib.error import HTTPError

from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.approval_lease import FileKeyLease, PostingJournal
from automation.interop.approval_lifecycle import (
    ApprovalIntent,
    ApprovalRequest,
    ApprovalSurfaceError,
    PostedApproval,
    Probe,
    Verdict,
    request_owner_approval,
)
from automation.interop.approval_surface import (
    ApprovalKind,
    RequestThread,
    resolve_new_binding,
    reuse_request_thread,
)
from automation.interop.reaction_approval import DiscordTransport

from .config import ObsidianWriteError
from .note_request import NoteRequest, NoteRequestStore
from .note_request_render import RENDER_VERSION, render_note_approval

APPROVE_EMOJI: Final = "\u2705"
CANCEL_EMOJI: Final = "\u26d4"
_TRANSPORT_ERRORS: Final = (OSError, ValueError, KeyError, TypeError, RuntimeError, HTTPError)


def _owner_reacted(users: tuple[tuple[str, bool], ...], owner_id: str) -> bool:
    return any(user_id == owner_id and not is_bot for user_id, is_bot in users)


@dataclass(frozen=True, slots=True)
class NoteApprovalGate:
    record: NoteRequest
    store: NoteRequestStore
    transport: DiscordTransport
    journal: PostingJournal | None = None

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        return tuple(
            ApprovalRequest(record.key, record.action_hash, record.message_id, record.channel_id, record.created_at)
            for record in self.store.all()
            if record.key == key and record.message_id and record.status in {"posted", "approved"}
        )

    def probe(self, request: ApprovalRequest) -> Probe:
        try:
            content = self.transport.get_message(request.channel_id, request.message_id)
            if content is None:
                return Probe.MISSING
            if request.action_hash not in content:
                return Probe.BINDING_MISMATCH
            cancelled = self.transport.get_reaction_users(request.channel_id, request.message_id, CANCEL_EMOJI)
            approved = self.transport.get_reaction_users(request.channel_id, request.message_id, APPROVE_EMOJI)
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error
        if _owner_reacted(cancelled, self.transport.owner_id):
            return Probe.CANCELLED
        if _owner_reacted(approved, self.transport.owner_id):
            return Probe.APPROVED
        return Probe.BOUND_PENDING

    def delete(self, request: ApprovalRequest) -> None:
        try:
            self.transport.delete_message(request.channel_id, request.message_id)
        except HTTPError as error:
            if error.code != 404:
                raise ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        self.store.clear_message(request.key, request.action_hash, request.message_id)

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        try:
            body = self.store.frozen_plan(self.record).body
            content = render_note_approval(self.record, body)
            message_id = self.transport.post_message(intent.channel_id, content)
            if self.journal is not None:
                self.journal.enrich(intent.key, intent.action_hash, message_id, intent.channel_id)
                self.store.set_message(self.record.request_id, message_id, intent.channel_id, RENDER_VERSION)
                self.journal.clear(intent.key)
        except (*_TRANSPORT_ERRORS, ObsidianWriteError) as error:
            raise ApprovalSurfaceError(str(error)) from error
        for emoji in (APPROVE_EMOJI, CANCEL_EMOJI):
            try:
                self.transport.add_reaction(intent.channel_id, message_id, emoji)
            except _TRANSPORT_ERRORS:
                continue
        return PostedApproval(message_id, intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        del intent, created_at
        self.store.set_message(self.record.request_id, posted.message_id, posted.channel_id, RENDER_VERSION)


@dataclass(frozen=True, slots=True)
class DiscordRuntime:
    transport: DiscordTransport
    directory: DiscordChannelDirectory
    owner_id: str


def discord_runtime(token: str, owner_id: str, store: NoteRequestStore) -> DiscordRuntime:
    if not token or not owner_id:
        raise ObsidianWriteError("Discord token or owner id is unavailable", False)
    transport = DiscordTransport(token, owner_id)
    directory = DiscordChannelDirectory(
        token, owner_id, transport.api, store.root / "approval-directory.json"
    )
    return DiscordRuntime(transport, directory, owner_id)


def request_approval(record: NoteRequest, store: NoteRequestStore, runtime: DiscordRuntime) -> Verdict:
    """Bind the per-request thread (one key keeps one thread) and post through the façade."""
    live = NoteApprovalGate(record, store, runtime.transport).outstanding(record.key)
    binding = reuse_request_thread(
        ApprovalKind.OBSIDIAN_WRITE, live, runtime.directory, runtime.owner_id
    ) or resolve_new_binding(
        ApprovalKind.OBSIDIAN_WRITE,
        runtime.directory,
        runtime.owner_id,
        request=RequestThread(
            title=PurePosixPath(record.relpath).name,
            origin_channel_id=record.origin_channel_id,
            origin_message_id=record.origin_message_id,
        ),
    )
    bound = replace(
        record,
        kind=binding.kind.value,
        surface=binding.surface.value,
        channel_id=binding.channel_id,
        policy_version=binding.policy_version,
        approval_thread_id=binding.channel_id,
        approval_guild_id=binding.guild_id or record.approval_guild_id,
    )
    store.update(bound)
    journal = PostingJournal(store.root / "posting-journal")
    gate = NoteApprovalGate(bound, store, runtime.transport, journal)
    intent = ApprovalIntent(key=bound.key, action_hash=bound.action_hash, channel_id=binding.channel_id)
    return request_owner_approval(intent, gate, FileKeyLease(store.root / "approval-leases"), journal)


def owner_id_from_config() -> str:
    import json
    from pathlib import Path

    path = Path(os.environ.get("INTEROP_CONFIG", str(Path.home() / ".hermes" / "interop" / "config.json")))
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("owner_id")
    except (OSError, ValueError, AttributeError):
        value = None
    if not isinstance(value, str) or not value:
        raise ObsidianWriteError("interop config has no owner_id (fail-closed)", False)
    return value
