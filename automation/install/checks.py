"""Shared check vocabulary for the install-time helpers.

Every prerequisite helper in this package answers with the same three-valued
verdict so a future installer (W-F1-B) can aggregate them without knowing what
each one checked. Pure data and pure rendering — no I/O lives here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class Status(StrEnum):
    """Outcome of a single check."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check's verdict plus an operator-actionable diagnosis."""

    name: str
    status: Status
    detail: str


def render(results: Sequence[CheckResult], *, verdict_label: str = "READY") -> str:
    """Render the report operators read; contains no credential material."""
    lines = [f"[{result.status}] {result.name}: {result.detail}" for result in results]
    failed = sum(1 for result in results if result.status is Status.FAIL)
    warned = sum(1 for result in results if result.status is Status.WARN)
    verdict = verdict_label if failed == 0 else f"NOT-{verdict_label}"
    lines.append(f"--- {verdict}: {len(results)}건 중 실패 {failed} / 경고 {warned}")
    return "\n".join(lines)


def exit_code(results: Sequence[CheckResult]) -> int:
    """Return 0 only when no check failed."""
    return 1 if any(result.status is Status.FAIL for result in results) else 0


def redact(text: str, secret: str) -> str:
    """Strip a credential from rendered output as a defence in depth."""
    return text.replace(secret, "<redacted>") if secret else text
