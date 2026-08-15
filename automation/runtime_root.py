#!/usr/bin/env python3
"""Runtime-root resolver (DG-4): the one place every runtime consumer resolves
where its code lives.

Resolution order:
  1. AUTOPHAGY_RUNTIME_ROOT in the environment (explicit override), else
  2. the immutable release symlink /srv/autophagy-agent-current, if it exists, else
  3. the resident /srv/autophagy-agents mirror (backwards-compatible fallback).

Because a missing `current` falls back to the mirror, migrating consumers to this
resolver is a behavioural NO-OP until the node rollout (DG-5) creates the symlink.
A tiny bash twin (runtime_root.sh) resolves identically for shell consumers; a
test pins them byte-for-byte.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final

RELEASE_CURRENT: Final = Path("/srv/autophagy-agent-current")
MIRROR_CHECKOUT: Final = Path("/srv/autophagy-agents")


def resolve_runtime_root(
    env: Mapping[str, str],
    *,
    current: Path = RELEASE_CURRENT,
    mirror: Path = MIRROR_CHECKOUT,
) -> Path:
    """Return the runtime root: env override, else `current` if present, else mirror."""
    override = env.get("AUTOPHAGY_RUNTIME_ROOT")
    if override:
        return Path(override)
    if current.exists():
        return current
    return mirror
