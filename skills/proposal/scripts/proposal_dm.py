"""Owner-DM delivery for a completed proposal final review."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


class DeliveryError(RuntimeError):
    """The owner review notification could not be delivered."""


def resolve_target(explicit: str = "") -> str:
    """Resolve a private `hermes send` target without committing a Discord id."""
    if explicit:
        return explicit
    configured = os.environ.get("PROPOSAL_DM_TARGET", "")
    if configured:
        return configured
    path = Path("~/.hermes/proposal/config.json").expanduser()
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise DeliveryError("proposal DM configuration is invalid") from error
        target = payload.get("dm_target") if isinstance(payload, dict) else None
        if isinstance(target, str) and target:
            return target
    if os.environ.get("PROPOSAL_DM_DISABLED") == "1":
        return ""
    raise DeliveryError("proposal DM target is required")


def _chunks(message: str) -> tuple[str, ...]:
    chunks: list[str] = []
    remaining = message
    while len(remaining) > 1800:
        boundary = remaining.rfind("\n", 0, 1800)
        split_at = boundary if boundary > 0 else 1800
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return (*chunks, remaining)


def send_review(target: str, message: str) -> None:
    """Send every review chunk through Hermes' configured Discord transport."""
    if not target:
        return
    environment = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"}
    for chunk in _chunks(message):
        try:
            completed = subprocess.run(
                ("hermes", "send", "--to", target, chunk),
                cwd=Path.home(),
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DeliveryError(error.__class__.__name__) from error
        if completed.returncode != 0:
            raise DeliveryError(f"owner DM rc={completed.returncode}")
