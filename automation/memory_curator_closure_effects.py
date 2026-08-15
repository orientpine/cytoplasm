"""Wiki-specific surface adapter for memory-curator settlement."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol


class SettledEditor(Protocol):
    def edit_settled(
        self,
        channel_id: str,
        message_id: str,
        action_hash: str,
        suffix: str,
    ) -> Literal["edited", "already-settled", "missing", "binding-mismatch"]: ...


@dataclass(frozen=True, slots=True)
class WikiClosureSurface:
    gate: SettledEditor

    def probe(self, channel_id: str, message_id: str, action_hash: str) -> Literal[
        "bound", "missing", "binding-mismatch", "unverifiable"
    ]:
        wiki_gate = importlib.import_module("wiki_gate")
        try:
            message = wiki_gate._confirm_message(channel_id, message_id)
        except (OSError, RuntimeError):
            return "unverifiable"
        if message is None:
            return "missing"
        content = str(message.get("content", ""))
        return "bound" if action_hash in content else "binding-mismatch"

    def edit_settled(
        self,
        channel_id: str,
        message_id: str,
        action_hash: str,
        suffix: str,
    ) -> Literal["edited", "already-settled", "missing", "binding-mismatch"]:
        return self.gate.edit_settled(channel_id, message_id, action_hash, suffix)


def build_surface() -> WikiClosureSurface:
    wiki_scripts = Path(__file__).resolve().parents[1] / "skills" / "wiki" / "scripts"
    if str(wiki_scripts) not in sys.path:
        sys.path.insert(0, str(wiki_scripts))
    module = importlib.import_module("wiki_approval")
    return WikiClosureSurface(module.WikiApprovalGate({}))
