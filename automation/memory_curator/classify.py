from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from automation.rag_ingest.sensitivity import SensitivityRule
from automation.twin_distill.llm import (
    LlmClient,
    LlmConfigurationError,
    LlmInvocationError,
)

from .classify_model import EntryVerdict
from .classify_parse import parse_verdict
from .classify_prompt import render
from .classify_veto import post_llm_veto, pre_llm_veto
from .model import MemoryEntry, MemoryKind

_KIND_ORDER: Final[tuple[MemoryKind, ...]] = ("memory", "user")


def classify_entries(
    entries_by_kind: Mapping[MemoryKind, tuple[MemoryEntry, ...]],
    *,
    client: LlmClient,
    rules: Sequence[SensitivityRule],
) -> tuple[EntryVerdict, ...]:
    verdicts: list[EntryVerdict] = []
    for kind in _KIND_ORDER:
        for entry in entries_by_kind.get(kind, ()):
            verdict = pre_llm_veto(entry.text, source_kind=kind, rules=rules)
            if verdict is not None:
                verdicts.append(verdict)
                continue

            try:
                raw = client.complete(render(entry.text, source_kind=kind))
            except (LlmInvocationError, LlmConfigurationError):
                verdicts.append(
                    EntryVerdict(
                        source_kind=kind,
                        entry_text=entry.text,
                        route="UNCERTAIN",
                        evidence="",
                        reason="",
                        veto="llm_error",
                        llm_called=True,
                    )
                )
                continue

            verdicts.append(
                post_llm_veto(
                    parse_verdict(raw, entry.text, source_kind=kind)
                )
            )
    return tuple(verdicts)
