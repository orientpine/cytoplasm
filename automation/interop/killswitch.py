"""File-backed, owner-gated pause state for Interop Protocol v0."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class KillSwitchResult:
    """The observable decision for one pause/resume command."""

    accepted: bool
    paused: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class PauseStore:
    """Persist a pause sentinel and accept commands from exactly one owner."""

    state_file: Path
    owner_id: str

    def is_paused(self) -> bool:
        """Return whether the persistent pause sentinel currently exists."""
        return self.state_file.exists()

    def handle(self, command: str, actor_id: str) -> KillSwitchResult:
        """Apply an owner-authorized pause or resume command."""
        if actor_id != self.owner_id:
            return KillSwitchResult(accepted=False, paused=self.is_paused(), reason="owner_required")
        if command == "!pause-agents":
            self.state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.state_file.write_text("paused\n", encoding="utf-8")
            self.state_file.chmod(0o600)
            return KillSwitchResult(accepted=True, paused=True, reason=None)
        if command == "!resume-agents":
            self.state_file.unlink(missing_ok=True)
            return KillSwitchResult(accepted=True, paused=False, reason=None)
        return KillSwitchResult(accepted=False, paused=self.is_paused(), reason="unknown_command")
