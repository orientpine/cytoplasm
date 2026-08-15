"""Manual repair-command parsing without routing ordinary conversation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ManualRepairCommand:
    message: str


def parse_repair_command(text: str) -> ManualRepairCommand | None:
    """Recognize the supported manual repair phrases and retain an opaque message."""
    normalized = text.strip()
    if normalized == "!repair":
        return ManualRepairCommand(message="manual !repair request")
    if normalized.startswith("!repair "):
        return ManualRepairCommand(message=normalized.removeprefix("!repair ").strip())
    if "수리해줘" in normalized or "이상해" in normalized:
        return ManualRepairCommand(message=normalized)
    return None
