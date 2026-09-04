"""Configuration and typed failures for the isolated Obsidian write clone."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

#: 120 s covers local git calls but killed every real fetch of the ~770 MB vault.
DEFAULT_FETCH_TIMEOUT_SECONDS: Final = 900.0
FETCH_TIMEOUT_ENV: Final = "OBSIDIAN_WRITE_FETCH_TIMEOUT"
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
    #: Budget for fetch and clone only; local git calls keep the short timeout.
    fetch_timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS
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
    return ObsidianWriteConfig(repo_url, clone_dir, ssh_key_path, branch, _fetch_timeout())


def _fetch_timeout() -> float:
    """Resolve the fetch/clone budget, which the owner may raise for a cold vault.

    This stays an environment override instead of a ``config.json`` key because the
    right value follows the machine's link speed, not the owner's vault provisioning.
    A malformed override fails closed: silently falling back to a short timeout is how
    the write clone spent a month killing its own fetches.
    """
    raw = os.environ.get(FETCH_TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_FETCH_TIMEOUT_SECONDS
    try:
        seconds = float(raw)
    except ValueError as error:
        raise ObsidianWriteError("Obsidian write fetch timeout is not a number", False) from error
    if not math.isfinite(seconds) or seconds <= 0:
        raise ObsidianWriteError("Obsidian write fetch timeout must be positive", False)
    return seconds


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ObsidianWriteError(f"Obsidian write configuration is missing {key}", False)
    return value.strip()


def _validate_branch(branch: str) -> None:
    valid_characters = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-")
    if branch.startswith("-") or ".." in branch or any(char not in valid_characters for char in branch):
        raise ObsidianWriteError("Obsidian write configuration has an invalid branch", False)
