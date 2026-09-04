"""Per-run GLM availability latch and non-GLM degrade for the digest.

2026-09-03, digest run 49: the provider behind ``glm-main`` answered HTTP 429
("Insufficient balance") to every call. Each of the 15 non-sensitive mails
retried its own way into the same outage (~40 s apiece) and then landed in the
owner's digest as ``(요약 실패)`` + ``⚠️ 분류 실패``, with no cause anywhere. The
four sensitive mails — already routed to the non-GLM tier — were fine, which is
the whole point: a working tier existed and nothing used it.

Two rules follow.

1. Degrade, don't fail: when a NON-sensitive step still fails with
   ``LlmUnavailableError`` after its single retry, the same prompt runs on the
   non-GLM tier. Today's fallbacks apply only when that tier fails too.
2. Latch per run: the first outage verdict is remembered for the rest of the
   run, so the remaining mails skip the retry storm. The latch is created in
   ``run_digest`` and passed down explicitly — a module-level singleton would
   carry an outage into the next run (and between tests).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

import triage_core
import triage_llm

REASON_LIMIT = 120  # owner-line budget for the masked cause
_T = TypeVar("_T")


@dataclass(slots=True)
class GlmLatch:
    """GLM availability state for ONE digest run (mutable by design).

    Not frozen: the latch exists to carry a verdict forward across the mails of
    a single run. Its lifetime is exactly one ``run_digest`` call.
    """

    tripped: bool = False
    reason: str = ""
    degraded_mails: int = 0

    def trip(self, error: BaseException) -> None:
        """Latch the FIRST outage and keep its masked, clipped reason."""
        if self.tripped:
            return
        self.tripped = True
        reason = triage_core.redact(str(error)).replace("\n", " ").strip()
        prefix = f"{triage_llm.GLM_MODEL} 호출 실패: "
        if reason.startswith(prefix):  # the owner line already names the tier
            reason = reason[len(prefix) :]
        self.reason = reason[:REASON_LIMIT]

    def count_degraded_mail(self) -> None:
        self.degraded_mails += 1

    def notice_line(self) -> str:
        """The single owner line for this run — '' while glm-main is healthy.

        Carries only the masked transport reason and a count: never a subject,
        an address, or a key.
        """
        if not self.tripped:
            return ""
        return (
            f"⚠️ glm-main 사용 불가 — {self.reason} · "
            f"비민감 {self.degraded_mails}건을 비-GLM 티어로 처리"
        )


def call_with_fallback(
    step: Callable[[bool], _T],
    *,
    latch: GlmLatch,
    sensitive: bool,
    retry_on: tuple[type[BaseException], ...],
) -> _T:
    """Run one LLM step, degrading to the non-GLM tier when glm-main is down.

    ``step(force_codex)`` performs the call. Sensitive mail never routes through
    GLM (constraint 6), so it keeps the plain single retry and leaves the latch
    untouched. For non-sensitive mail: probe GLM once with one retry, and on
    ``LlmUnavailableError`` latch the run and rerun the same step on the non-GLM
    tier. Failures from that tier propagate to the caller's existing fallbacks.
    """
    if sensitive:
        return _retry_once(step, force_codex=False, retry_on=retry_on)
    if latch.tripped:
        return step(True)  # already judged unavailable — do not burn the retries again
    try:
        return _retry_once(step, force_codex=False, retry_on=retry_on)
    except triage_llm.LlmUnavailableError as error:
        latch.trip(error)
        return step(True)


def _retry_once(
    step: Callable[[bool], _T],
    *,
    force_codex: bool,
    retry_on: tuple[type[BaseException], ...],
) -> _T:
    """The existing single retry, unchanged in count and in exception scope."""
    try:
        return step(force_codex)
    except retry_on:
        return step(force_codex)
