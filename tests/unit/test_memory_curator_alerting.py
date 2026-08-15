from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Final

import pytest

from automation.memory_curator.alerting import (
    ActionableState,
    AlertOutcome,
    bucket_for,
    decide_alert,
    signature,
)
from automation.memory_curator.state import AlertState


NOW: Final = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
NOW_TEXT: Final = "2026-07-30T12:00:00Z"
ACTIONABLE_SIGNATURE: Final = signature(
    ActionableState(buckets={"memory": "near", "user": "ok"}, entries={}, manual_reasons=())
)
CHANGED_SIGNATURE: Final = signature(
    ActionableState(
        buckets={"memory": "critical", "user": "ok"}, entries={}, manual_reasons=()
    )
)
QUIET_SIGNATURE: Final = signature(
    ActionableState(buckets={"memory": "ok", "user": "ok"}, entries={}, manual_reasons=())
)


@pytest.mark.parametrize(
    ("fill_ratio", "expected"),
    [
        (0.849, "ok"),
        (0.85, "near"),
        (0.949, "near"),
        (0.95, "critical"),
        (1.0, "over"),
        (1.5, "over"),
    ],
)
def test_bucket_for_when_ratio_crosses_a_boundary_uses_the_coarse_bucket(
    fill_ratio: float,
    expected: str,
) -> None:
    # Given: a fill ratio at or around an alerting boundary.
    # When: the ratio is reduced to its coarse alerting bucket.
    actual = bucket_for(fill_ratio)

    # Then: only the documented boundary determines the bucket.
    assert actual == expected


def test_signature_when_mapping_order_and_reason_duplicates_differ_is_canonical() -> None:
    # Given: equivalent actionable facts in different insertion orders.
    first = ActionableState(
        buckets={"memory": "near", "user": "critical"},
        entries={"entry-a": "unproposed", "entry-b": "verification_blocked"},
        manual_reasons=("reason-b", "reason-a", "reason-a"),
    )
    second = ActionableState(
        buckets={"user": "critical", "memory": "near"},
        entries={"entry-b": "verification_blocked", "entry-a": "unproposed"},
        manual_reasons=("reason-a", "reason-b"),
    )
    canonical_json = (
        b'{"buckets":{"memory":"near","user":"critical"},'
        b'"entries":{"entry-a":"unproposed","entry-b":"verification_blocked"},'
        b'"manual_reasons":["reason-a","reason-b"]}'
    )

    # When: both states are signed.
    first_signature = signature(first)
    second_signature = signature(second)

    # Then: insertion order and duplicates add no noise to the canonical digest.
    assert first_signature == second_signature
    assert first_signature == sha256(canonical_json).hexdigest()


@pytest.mark.parametrize(
    "changed",
    [
        ActionableState(
            buckets={"memory": "critical", "user": "ok"},
            entries={"entry-a": "unproposed"},
            manual_reasons=(),
        ),
        ActionableState(
            buckets={"memory": "near", "user": "ok"},
            entries={"entry-a": "awaiting_artifact"},
            manual_reasons=(),
        ),
        ActionableState(
            buckets={"memory": "near", "user": "ok"},
            entries={"entry-a": "unproposed"},
            manual_reasons=("owner-review",),
        ),
    ],
    ids=("bucket", "entry-status", "manual-reason"),
)
def test_signature_when_an_actionable_fact_changes_produces_a_new_digest(
    changed: ActionableState,
) -> None:
    # Given: one baseline actionable state.
    baseline = ActionableState(
        buckets={"memory": "near", "user": "ok"},
        entries={"entry-a": "unproposed"},
        manual_reasons=(),
    )

    # When: a bucket, entry status, or manual reason changes.
    changed_signature = signature(changed)

    # Then: the change is visible to alert deduplication.
    assert changed_signature != signature(baseline)


def test_decide_alert_when_three_ticks_are_identical_sends_only_once_forever() -> None:
    # Given: no alert has ever been sent.
    prior = AlertState(None, None, None, None)

    # When: the same actionable signature is observed three times, including after cooldown.
    first = decide_alert(ACTIONABLE_SIGNATURE, prior, NOW)
    second = decide_alert(ACTIONABLE_SIGNATURE, first.next_alert_state, NOW + timedelta(minutes=30))
    third = decide_alert(ACTIONABLE_SIGNATURE, second.next_alert_state, NOW + timedelta(hours=25))

    # Then: the first tick sends and unchanged later ticks stay silent forever.
    assert (first.decision, second.decision, third.decision) == ("send", "silent", "silent")
    assert third.next_alert_state == AlertState(
        ACTIONABLE_SIGNATURE, ACTIONABLE_SIGNATURE, NOW_TEXT, None
    )


def test_decide_alert_when_signature_changes_inside_cooldown_holds_then_sends() -> None:
    # Given: a different signature was successfully sent one hour ago.
    sent_at = NOW - timedelta(hours=1)
    sent_at_text = sent_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    prior = AlertState(ACTIONABLE_SIGNATURE, ACTIONABLE_SIGNATURE, sent_at_text, None)

    # When: the changed signature remains current through the cooldown boundary.
    held = decide_alert(CHANGED_SIGNATURE, prior, NOW)
    sent = decide_alert(CHANGED_SIGNATURE, held.next_alert_state, NOW + timedelta(hours=23))

    # Then: it is pending during cooldown and sent exactly when cooldown elapses.
    assert held == AlertOutcome(
        "hold",
        AlertState(CHANGED_SIGNATURE, ACTIONABLE_SIGNATURE, sent_at_text, CHANGED_SIGNATURE),
    )
    assert sent == AlertOutcome(
        "send",
        AlertState(CHANGED_SIGNATURE, CHANGED_SIGNATURE, "2026-07-31T11:00:00Z", None),
    )


@pytest.mark.parametrize(
    "prior",
    [
        AlertState(None, None, None, None),
        AlertState(ACTIONABLE_SIGNATURE, ACTIONABLE_SIGNATURE, NOW_TEXT, CHANGED_SIGNATURE),
    ],
    ids=("never-sent", "previously-sent"),
)
def test_decide_alert_when_state_is_quiet_is_always_silent(prior: AlertState) -> None:
    # Given: a quiet signature and any prior alert history.
    # When: alerting evaluates the quiet state.
    outcome = decide_alert(QUIET_SIGNATURE, prior, NOW + timedelta(days=2))

    # Then: it never sends, preserves sent history, and clears pending work.
    assert outcome == AlertOutcome(
        "silent",
        AlertState(QUIET_SIGNATURE, prior.last_sent_signature, prior.last_sent_at, None),
    )


def test_decide_alert_when_first_actionable_state_arrives_sends_immediately() -> None:
    # Given: an all-None alert history.
    # When: the first actionable signature arrives.
    outcome = decide_alert(ACTIONABLE_SIGNATURE, AlertState(None, None, None, None), NOW)

    # Then: it is sent and the successful-send state is ready to persist.
    assert outcome == AlertOutcome(
        "send",
        AlertState(ACTIONABLE_SIGNATURE, ACTIONABLE_SIGNATURE, NOW_TEXT, None),
    )


def test_decide_alert_when_pending_change_reverts_clears_pending_and_stays_silent() -> None:
    # Given: a changed signature was held after the last successful send.
    prior = AlertState(CHANGED_SIGNATURE, ACTIONABLE_SIGNATURE, NOW_TEXT, CHANGED_SIGNATURE)

    # When: the current state reverts to the last successfully sent signature.
    outcome = decide_alert(ACTIONABLE_SIGNATURE, prior, NOW + timedelta(minutes=30))

    # Then: the stale pending signature is cleared without another send.
    assert outcome == AlertOutcome(
        "silent",
        AlertState(ACTIONABLE_SIGNATURE, ACTIONABLE_SIGNATURE, NOW_TEXT, None),
    )
