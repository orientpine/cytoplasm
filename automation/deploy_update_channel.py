"""Roster-backed update-channel selection and root-helper binding."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_ROSTER_ENV: Final = "AUTOPHAGY_ROSTER"
_READ_TIMEOUT: Final = 10.0


@dataclass(frozen=True, slots=True)
class UpdateChannelSource:
    roster_path: Path
    agent_account: str


def read_roster_update_channel(
    source: UpdateChannelSource,
    path: Path | None = None,
) -> str | None:
    """Return a validated explicit channel; unavailable rosters preserve upstream."""
    try:
        from automation.group_roster import RosterError, load_roster, parse_roster
    except ImportError:
        return None
    configured = os.environ.get(_ROSTER_ENV, "").strip()
    if path is not None or configured:
        selected_path = path or Path(configured).expanduser()
        try:
            return load_roster(selected_path).update_channel
        except RosterError:
            return None
    try:
        completed = subprocess.run(
            (
                "sudo",
                "-n",
                "-u",
                source.agent_account,
                "-H",
                "/usr/bin/cat",
                "--",
                str(source.roster_path),
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=_READ_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        return parse_roster(completed.stdout, source=str(source.roster_path)).update_channel
    except RosterError:
        return None


def save_update_channel_binding(update_channel: str | None, path: Path) -> None:
    """Atomically bind the privileged helper to this tick's selected channel."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    payload = json.dumps(
        {"update_channel": update_channel, "version": 1},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent,
        prefix=".update-channel-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            _ = stream.write(payload + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def with_update_channel(
    command: Sequence[str],
    update_channel: str | None,
) -> tuple[str, ...]:
    if update_channel is None:
        return tuple(command)
    return (
        "env",
        "GIT_CONFIG_COUNT=1",
        "GIT_CONFIG_KEY_0=remote.origin.url",
        f"GIT_CONFIG_VALUE_0={update_channel}",
        *command,
    )
