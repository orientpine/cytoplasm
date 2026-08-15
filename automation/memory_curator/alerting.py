from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Final, Literal

from .model import MemoryKind
from .state import AlertState

NearCapBucket = Literal["ok", "near", "critical", "over"]
EntryStatus = Literal[
    "unproposed",
    "awaiting_artifact",
    "verification_blocked",
    "legacy_unbound",
    "declined",
]
AlertDecision = Literal["send", "hold", "silent"]

_UTC_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"
_DEFAULT_COOLDOWN: Final = timedelta(hours=24)


def bucket_for(fill_ratio: float) -> NearCapBucket:
    if fill_ratio >= 1.0:
        return "over"
    if fill_ratio >= 0.95:
        return "critical"
    if fill_ratio >= 0.85:
        return "near"
    return "ok"


@dataclass(frozen=True, slots=True)
class ActionableState:
    buckets: Mapping[MemoryKind, NearCapBucket]
    entries: Mapping[str, EntryStatus]
    manual_reasons: tuple[str, ...]


def signature(s: ActionableState) -> str:
    normalized = {
        "buckets": {kind: s.buckets[kind] for kind in sorted(s.buckets)},
        "entries": {key: s.entries[key] for key in sorted(s.entries)},
        "manual_reasons": sorted(set(s.manual_reasons)),
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


_QUIET_SIGNATURE: Final = signature(
    ActionableState(
        buckets={"memory": "ok", "user": "ok"},
        entries={},
        manual_reasons=(),
    )
)


@dataclass(frozen=True, slots=True)
class AlertOutcome:
    decision: AlertDecision
    next_alert_state: AlertState


def decide_alert(
    current_signature: str,
    prior: AlertState,
    now: datetime,
    *,
    cooldown: timedelta = _DEFAULT_COOLDOWN,
) -> AlertOutcome:
    if current_signature == _QUIET_SIGNATURE:
        return AlertOutcome(
            decision="silent",
            next_alert_state=AlertState(
                last_observed_signature=current_signature,
                last_sent_signature=prior.last_sent_signature,
                last_sent_at=prior.last_sent_at,
                pending_signature=None,
            ),
        )

    if current_signature == prior.last_sent_signature:
        return AlertOutcome(
            decision="silent",
            next_alert_state=AlertState(
                last_observed_signature=current_signature,
                last_sent_signature=prior.last_sent_signature,
                last_sent_at=prior.last_sent_at,
                pending_signature=None,
            ),
        )

    if prior.last_sent_signature is None:
        return AlertOutcome(
            decision="send",
            next_alert_state=AlertState(
                last_observed_signature=current_signature,
                last_sent_signature=current_signature,
                last_sent_at=now.strftime(_UTC_FORMAT),
                pending_signature=None,
            ),
        )

    if prior.last_sent_at is None or (
        now - datetime.strptime(prior.last_sent_at, _UTC_FORMAT).replace(tzinfo=UTC)
        >= cooldown
    ):
        return AlertOutcome(
            decision="send",
            next_alert_state=AlertState(
                last_observed_signature=current_signature,
                last_sent_signature=current_signature,
                last_sent_at=now.strftime(_UTC_FORMAT),
                pending_signature=None,
            ),
        )

    return AlertOutcome(
        decision="hold",
        next_alert_state=AlertState(
            last_observed_signature=current_signature,
            last_sent_signature=prior.last_sent_signature,
            last_sent_at=prior.last_sent_at,
            pending_signature=current_signature,
        ),
    )
