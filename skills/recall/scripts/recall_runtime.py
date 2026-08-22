"""Where recall resolves `automation.*` from — the release, never the stale mirror.

Beside recall_cli (not under automation/) because it must answer BEFORE sys.path
reaches the repo, and separate from recall_cli so that module stays under the F2
pure-LOC ceiling.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

RELEASE_CURRENT: Final = Path("/srv/autophagy-agent-current")
MIRROR_CHECKOUT: Final = Path("/srv/autophagy-agents")


def runtime_root(
    env: Mapping[str, str] | None = None,
    *,
    current: Path = RELEASE_CURRENT,
    mirror: Path = MIRROR_CHECKOUT,
) -> Path:
    """DG-4 order: override, else the immutable release, else the mirror.

    The mirror is never the default: it freezes while dirty and, landing first on
    sys.path, shadows the release's `automation` (2026-08-22: frozen at b6b3574 with
    no automation/knowledge, so the deployed recall died at import).
    """
    environment = os.environ if env is None else env
    for name in ("AUTOPHAGY_REPO_ROOT", "AUTOPHAGY_RUNTIME_ROOT"):
        override = (environment.get(name) or "").strip()
        if override:
            return Path(override).expanduser()
    return current if current.is_dir() else mirror
