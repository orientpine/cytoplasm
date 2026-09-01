"""Structural ports the todo approval adapter talks through.

Held apart from ``todo_approval`` so that module stays under the 250 pure-LOC ceiling
without moving any of the approval logic the conformance inventory binds to it. Nothing
here has behaviour — these are ``Protocol`` declarations only, and ``todo_approval``
re-exports the two its own signatures name, so existing importers are unaffected.
"""
from __future__ import annotations

from typing import Protocol


class ChannelFactsLike(Protocol):
    channel_type: int
    name: str
    recipient_ids: tuple[str, ...]


class DirectoryLike(Protocol):
    def owner_dm(self) -> str: ...

    def skill_approvals(self) -> str: ...

    def describe(self, channel_id: str) -> ChannelFactsLike: ...


class TransportLike(Protocol):
    def post_message(self, channel_id: str, content: str) -> str: ...

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None: ...

    def get_message(self, channel_id: str, message_id: str) -> str | None: ...

    def get_reaction_users(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> tuple[tuple[str, bool], ...]: ...

    def delete_message(self, channel_id: str, message_id: str) -> None: ...
