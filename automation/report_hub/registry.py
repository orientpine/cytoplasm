"""Classification-only peer registry loaded from a private runtime file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

SUPPORTED_VERSION = 1


class RegistryError(ValueError):
    """The peers file is missing, malformed, or has an unsupported version."""


@dataclass(frozen=True, slots=True)
class Peer:
    """One registered team agent."""

    agent_id: str
    bot_user_id: str
    bot_name: str


@dataclass(frozen=True, slots=True)
class PeerRegistry:
    """Immutable lookup table over the registered peers."""

    peers: tuple[Peer, ...]

    def agent_id_for(self, bot_user_id: str) -> str | None:
        """Return the registered agent_id for a Discord bot user id, if any."""
        for peer in self.peers:
            if peer.bot_user_id == bot_user_id:
                return peer.agent_id
        return None


def load_registry(path: Path) -> PeerRegistry:
    """Parse and strictly validate the peers.yaml registry."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RegistryError(f"cannot read peers file {path}: {error}") from error
    if not isinstance(raw, dict) or raw.get("version") != SUPPORTED_VERSION:
        raise RegistryError(f"peers file {path} must declare version: {SUPPORTED_VERSION}")
    peers_node = raw.get("peers")
    if not isinstance(peers_node, dict) or not peers_node:
        raise RegistryError(f"peers file {path} must define a non-empty peers mapping")
    peers: list[Peer] = []
    for agent_id, entry in peers_node.items():
        if not isinstance(agent_id, str) or not agent_id:
            raise RegistryError(f"peers file {path}: agent_id keys must be non-empty strings")
        if not isinstance(entry, dict):
            raise RegistryError(f"peers file {path}: peer {agent_id} must be a mapping")
        bot_user_id = entry.get("bot_user_id")
        bot_name = entry.get("bot_name")
        if not isinstance(bot_user_id, str) or not bot_user_id.isdigit():
            raise RegistryError(f"peers file {path}: peer {agent_id} needs a numeric string bot_user_id")
        if not isinstance(bot_name, str) or not bot_name:
            raise RegistryError(f"peers file {path}: peer {agent_id} needs a non-empty bot_name")
        peers.append(Peer(agent_id=agent_id, bot_user_id=bot_user_id, bot_name=bot_name))
    ids = [peer.bot_user_id for peer in peers]
    if len(ids) != len(set(ids)):
        raise RegistryError(f"peers file {path}: duplicate bot_user_id entries")
    return PeerRegistry(peers=tuple(peers))
