"""Owner-gated write adapter for the dedicated Obsidian vault clone."""

from __future__ import annotations

from .config import (
    DEFAULT_BRANCH,
    DEFAULT_CONFIG_PATH,
    DEFAULT_WRITE_CLONE_DIR,
    DEFAULT_WRITE_KEY_PATH,
    READ_ONLY_MIRROR_DIR,
    ObsidianWriteConfig,
    ObsidianWriteError,
    load_config,
)
from .note import NotePlan, plan_note
from .writer import WriteReceipt, write_note

__all__ = (
    "DEFAULT_BRANCH",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_WRITE_CLONE_DIR",
    "DEFAULT_WRITE_KEY_PATH",
    "READ_ONLY_MIRROR_DIR",
    "NotePlan",
    "ObsidianWriteConfig",
    "ObsidianWriteError",
    "WriteReceipt",
    "load_config",
    "plan_note",
    "write_note",
)
