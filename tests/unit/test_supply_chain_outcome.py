"""Reading what the pipeline said, after a resume ran.

FA-3. The watcher re-invokes ``deploy-skill.sh`` and then has to decide what that
attempt meant. The decision is entirely in the exit code, and this repo learned the
hard way — in this same cycle — that two of those codes look alike and mean opposite
things:

* **8** is ``EXECUTION-LOCK-BLOCK``: another execution holds this skill's lease. Nothing
  is wrong, someone else is simply mid-deploy. Retry.
* **9** is the owner cancelling. The decision has been made. Retire.

Reading one as the other is not a cosmetic error. Treating contention as cancellation
retires a request whose approval is still live and still wanted; treating cancellation
as contention re-invokes the pipeline against a deploy the owner stopped, forever.

Everything unrecognised maps to ``failed``, which retains the record. Nothing maps to a
silent drop: an approval the owner already gave must never disappear because the
pipeline returned a number this table had not met before.
"""
from __future__ import annotations

import pytest

from automation.supply_chain_effects import (
    CANCELLED_EXIT,
    LEASE_CONTENTION_EXIT,
    interpret_resume,
)


def test_a_clean_run_is_done() -> None:
    assert interpret_resume(0) == "done"


def test_lease_contention_is_a_silent_retry() -> None:
    """Contention proves a deploy IS running; paging about it would be noise."""
    assert LEASE_CONTENTION_EXIT == 8
    assert interpret_resume(LEASE_CONTENTION_EXIT) == "retry"


def test_owner_cancellation_settles_the_request() -> None:
    """Same word the planner uses — the two vocabularies must not drift apart."""
    assert CANCELLED_EXIT == 9
    assert interpret_resume(CANCELLED_EXIT) == "settled"


def test_the_two_lookalike_codes_never_collapse() -> None:
    """The distinction this whole table exists for."""
    assert LEASE_CONTENTION_EXIT != CANCELLED_EXIT
    assert interpret_resume(LEASE_CONTENTION_EXIT) != interpret_resume(CANCELLED_EXIT)


@pytest.mark.parametrize("code", [1, 2, 3, 4, 5, 6, 7, 10, 42, 255])
def test_every_other_code_is_a_surfaced_failure(code: int) -> None:
    assert interpret_resume(code) == "failed"


@pytest.mark.parametrize("code", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 42, 255])
def test_no_code_silently_drops_the_request(code: int) -> None:
    """An approval the owner gave must never vanish because of an unfamiliar number."""
    assert interpret_resume(code) in {"done", "retry", "settled", "failed"}


def test_only_a_clean_run_or_a_cancellation_ends_the_request() -> None:
    """Everything else keeps the record so the next tick — or a human — can act."""
    ending = {code for code in range(0, 256) if interpret_resume(code) in {"done", "settled"}}
    assert ending == {0, CANCELLED_EXIT}


def test_the_outcome_vocabulary_does_not_drift_from_the_planner() -> None:
    """These two tables name the same states and must keep naming them the same way.

    They drifted once already: the planner's action was renamed and this table kept
    returning the old word, which only surfaced when the composition test ran both
    together. An invariant that holds by coincidence is the one that rots, so it is
    asserted here instead of being left to a downstream test to notice.
    """
    from automation.supply_chain_plan import RETAIN, SETTLED

    produced = {interpret_resume(code) for code in range(0, 256)}
    assert SETTLED in produced
    assert produced <= {"done", "retry", SETTLED, "failed"}
    # `retain` belongs to the planner alone: a run happened, so "no answer yet" is not
    # something an exit code can mean.
    assert RETAIN not in produced
