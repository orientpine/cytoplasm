"""Per-call retry policy for the digest — one tier, no degrade, fail closed.

2026-09-03, digest run 49: the provider behind the retired second tier answered
HTTP 429 ("Insufficient balance") to every call. Each of the 15 non-sensitive
mails retried its own way into the same outage (~40 s apiece) and then landed in
the owner's digest as ``(요약 실패)`` + ``⚠️ 분류 실패``, with no cause anywhere. The
repair of that day degraded non-sensitive mail onto the other tier of the time
and latched the outage verdict for the rest of the run.

2026-09-04 removed the second tier: every mail now runs on the Codex OAuth tier
(``triage_llm``), so "route it somewhere else" is no longer an available answer.
What survives is what was always correct, and what replaces the degrade:

1. ONE retry for a per-request failure (an unparseable answer, a one-off
   non-zero exit) — unchanged in count and in exception scope.
2. Fail closed on ``LlmUnavailableError``: when the tier itself cannot answer,
   the step is NOT retried (the incident's retry storm) and NOT rerouted (there
   is nowhere to route to). It propagates so the caller refuses the run instead
   of publishing a degraded digest.

There is no run-scoped state left to carry an outage forward — and therefore
none that could leak into the next run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import triage_llm

_T = TypeVar("_T")


def call_with_retry(
    step: Callable[[], _T],
    *,
    retry_on: tuple[type[BaseException], ...],
) -> _T:
    """Run one LLM step, retrying once on a per-request failure only.

    ``LlmUnavailableError`` is checked first because it subclasses
    ``LlmCallError``: a tier outage must escape even when the caller's
    ``retry_on`` would otherwise swallow it into a second doomed attempt.
    """
    try:
        return step()
    except triage_llm.LlmUnavailableError:
        raise
    except retry_on:
        return step()
