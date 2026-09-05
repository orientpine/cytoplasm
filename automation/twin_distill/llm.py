"""Fail-closed LLM seam for on-demand distillation — Codex OAuth is the only tier.

The ``LlmClient`` Protocol and the two error types are the injection points that
twin-distill, wiki-curate, memory-curator and memory-relocate share, so they are
unchanged.  The concrete client delegates to :mod:`automation.codex_llm`: one
provider, one subprocess call, no retry and no alternate tier.  A tier that
cannot answer raises here instead of routing the prompt anywhere else.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from automation.codex_llm import CodexClient, CodexError, CodexUnavailableError

_UNAVAILABLE: Final = "Codex OAuth tier is unavailable"


@dataclass(frozen=True, slots=True)
class LlmConfigurationError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class LlmInvocationError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


class LlmClient(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True, slots=True)
class CodexLlmClient:
    """Adapts the shared Codex OAuth client onto the ``LlmClient`` Protocol."""

    client: CodexClient

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> CodexLlmClient:
        """Bind the Codex OAuth tier, refusing when it cannot be reached at all."""
        try:
            return cls(CodexClient.from_environment(environment))
        except CodexUnavailableError as error:
            raise LlmConfigurationError(f"{_UNAVAILABLE}: {error}") from None

    def complete(self, prompt: str) -> str:
        try:
            return self.client.complete(prompt)
        except CodexUnavailableError as error:
            raise LlmConfigurationError(f"{_UNAVAILABLE}: {error}") from None
        except CodexError as error:
            raise LlmInvocationError(f"Codex request failed: {error}") from None
