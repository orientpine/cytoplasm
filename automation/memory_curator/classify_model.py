from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from .model import MemoryKind

Route = Literal["TWIN", "OPS_REFERENCE", "KEEP_NATIVE", "UNCERTAIN"]
VetoReason = Literal[
    "sensitivity",
    "credential",
    "keep_native_rule",
    "marker",
    "too_short",
    "user_file",
    "parse",
    "llm_error",
]

#: All valid routes, derived from the Route Literal so it can never drift.
ROUTES: Final[frozenset[str]] = frozenset(
    {"TWIN", "OPS_REFERENCE", "KEEP_NATIVE", "UNCERTAIN"}
)


@dataclass(frozen=True, slots=True)
class EntryVerdict:
    source_kind: MemoryKind
    entry_text: str
    route: Route
    evidence: str
    reason: str
    veto: VetoReason | None
    llm_called: bool
