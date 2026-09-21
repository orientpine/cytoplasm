"""Recovery boundaries for the real digest -> calendar -> watcher scenario."""
from __future__ import annotations

import importlib

import pytest

from tests.unit.test_calendar_digest_daily_thread import (
    CORRECTED, TEXTS, digest, pending, tick,
)
from tests.unit.test_calendar_digest_daily_thread import flow as flow
from tests.unit.test_calendar_single_live_request import calendar_env as calendar_env
from tests.unit.test_calendar_single_live_request import calendar_gate


def test_digest_retains_retry_draft_when_thread_resolution_fails(flow, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a directory failure before an approval thread can be resolved.
    binding = importlib.import_module("calendar_digest")

    def unavailable(_draft):
        raise calendar_gate.GateError("fixture directory unavailable", 1)

    with monkeypatch.context() as outage:
        outage.setattr(binding, "daily_binding", unavailable)
        digest(flow, (TEXTS[0],))
    assert flow[3]
    # When the next watcher tick runs with the directory recovered.
    tick(flow)
    # Then the saved draft becomes exactly one live card.
    assert len(pending()) == 1
    assert len(flow[0].request_threads) == 1


@pytest.mark.parametrize("reaction", ["✅", "⛔"])
def test_digest_preserves_decided_payload_when_correction_precedes_watcher(flow, reaction: str) -> None:
    # Given a decision that the watcher has not consumed yet.
    digest(flow, (TEXTS[1],))
    [entry] = pending()
    flow[4][entry.dm_message_id] = reaction
    # When a correction arrives before the decision is consumed.
    digest(flow, (CORRECTED,))
    # Then the originally approved payload remains executable, not replaced.
    assert calendar_gate.load_draft(entry.draft_id)["sha256"] == entry.sha256
    assert flow[0].posts == 1


def test_digest_recovers_previous_publication_when_new_correction_arrives(flow) -> None:
    # Given an uncertain card POST and a durable draft awaiting recovery.
    flow[5].append("after")
    digest(flow, (TEXTS[1],))
    assert pending() == ()
    # When another correction arrives before the retry tick.
    digest(flow, (CORRECTED,))
    # Then the uncertain publication is reconciled before replacing it.
    assert len(pending()) == 1
    assert len(flow[1].keys() - flow[0].deleted) == 1
    [entry] = pending()
    assert calendar_gate.load_draft(entry.draft_id)["end"].endswith("17:00:00+09:00")
