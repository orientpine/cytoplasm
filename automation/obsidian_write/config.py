"""Configuration and typed failures for the isolated Obsidian write clone."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

DEFAULT_CONFIG_PATH: Final = Path.home() / ".hermes" / "obsidian-write" / "config.json"
DEFAULT_WRITE_CLONE_DIR: Final = Path.home() / ".hermes" / "obsidian-write"
DEFAULT_WRITE_KEY_PATH: Final = Path.home() / ".ssh" / "obsidian_write_key"
DEFAULT_BRANCH: Final = "main"
READ_ONLY_MIRROR_DIR: Final = Path.home() / ".hermes" / "obsidian-mirror"


class PushGuard(Protocol):
    """Owner-approval integration executed immediately before the git push."""

    def __call__(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ObsidianWriteError(Exception):
    """A fail-closed write failure with an explicit retry classification."""

    message: str
    retryable: bool

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class ObsidianWriteConfig:
    """Immutable configuration for the dedicated write clone and deploy key."""

    repo_url: str
    clone_dir: Path = DEFAULT_WRITE_CLONE_DIR
    ssh_key_path: Path = DEFAULT_WRITE_KEY_PATH
    branch: str = DEFAULT_BRANCH
    push_guard: PushGuard | None = field(default=None, repr=False, compare=False)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> ObsidianWriteConfig:
    """Load the owner-provisioned write configuration without exposing its contents."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ObsidianWriteError("Obsidian write configuration is missing or unreadable", False) from error

    if not isinstance(payload, dict):
        raise ObsidianWriteError("Obsidian write configuration must be an object", False)

    repo_url = _required_string(payload, "repo_url")
    clone_dir = Path(_required_string(payload, "clone_dir")).expanduser()
    ssh_key_path = Path(_required_string(payload, "ssh_key_path")).expanduser()
    branch = _required_string(payload, "branch")
    _validate_branch(branch)
    return ObsidianWriteConfig(repo_url, clone_dir, ssh_key_path, branch)


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ObsidianWriteError(f"Obsidian write configuration is missing {key}", False)
    return value.strip()


def _validate_branch(branch: str) -> None:
    valid_characters = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-")
    if branch.startswith("-") or ".." in branch or any(char not in valid_characters for char in branch):
        raise ObsidianWriteError("Obsidian write configuration has an invalid branch", False)
