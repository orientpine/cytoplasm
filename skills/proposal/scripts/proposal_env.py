"""Load provisioned proposal secrets for tool subprocesses without a gateway restart.

The gateway reads ``~/.env.secrets`` as an EnvironmentFile, but its already-running tool
subprocesses do not receive keys provisioned after startup. The provisioner deliberately restarts
nothing, so the proposal CLI fills absent proposal and docbot keys from that file on startup.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_secrets(
    path: Path | None = None,
    *,
    prefixes: tuple[str, ...] = ("PROPOSAL_", "KIMM_DOCBOT_"),
) -> tuple[str, ...]:
    """Fill absent prefixed environment keys from an env file."""
    try:
        lines = (Path.home() / ".env.secrets" if path is None else path).read_text(
            encoding="utf-8"
        ).splitlines()
    except (OSError, UnicodeError):
        return ()

    loaded: list[str] = []
    for raw in lines:
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not key.startswith(prefixes) or key in os.environ:
            continue
        try:
            os.environ[key] = value.strip().strip('"').strip("'")
        except (TypeError, ValueError):
            continue
        loaded.append(key)
    return tuple(loaded)
