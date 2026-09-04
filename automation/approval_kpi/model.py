"""Value types shared by the readers, the aggregator, and the CLI."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ApprovalEvent:
    """One owner-approval request as some ledger recorded it.

    ``created_at`` is when the owner was asked, ``decided_at`` is when the decision was
    recorded. A ledger that stores only one of the two yields ``decided_at=None``: the
    wait is unknown, and an unknown wait is never counted as a zero-second wait.
    """

    kind: str
    surface: str
    created_at: datetime
    decided_at: datetime | None
    decision: str
    manual_reaction: bool
    request_key: str

    @property
    def wait_seconds(self) -> float | None:
        """Seconds between the request and its decision, or None when undecided."""
        if self.decided_at is None:
            return None
        return (self.decided_at - self.created_at).total_seconds()


@dataclass(frozen=True, slots=True)
class KindStats:
    """Aggregate approval burden for one kind over the observed span."""

    kind: str
    count: int
    decided: int
    per_day: float
    p50_seconds: float | None
    p95_seconds: float | None
    rerequest_rate: float
    manual_reaction_rate: float
