"""One watcher tick, composed end to end — with the subprocess injected.

FA-3. Everything the watcher decides now exists in pieces: enumerate the records, plan
what each one means, build the one command a resume may run, read what the attempt
meant. This is the composition, and composing is where the remaining mistakes live —
running the wrong plan, running one twice, letting one failure eat the rest of the tick.

The runner is injected, so this pins the whole flow without a subprocess ever existing.
What it must guarantee:

* **Only an approved request runs anything.** A settled or retained plan must not reach
  the runner at all — a watcher that shells out for a request the owner refused is the
  failure the settled action was introduced to prevent.
* **Exactly once per request per tick.** Twice would race the pipeline's own per-skill
  execution lock against itself.
* **One request's failure does not eat the others.** The tick processes a directory; if
  a raised exception aborted it, one broken record would starve every other approval
  indefinitely — silently, which is the failure mode this whole feature exists to remove.
"""
from __future__ import annotations

import json
from pathlib import Path

from automation.supply_chain_plan import SETTLED, PendingRequest
from automation.supply_chain_reconcile import HOLD, RETIRE_DONE, RUN, Reconciled
from automation.supply_chain_watch import (
    FailureAttempt,
    TickResult,
    retry_due,
    update_failure,
    watch_tick,
)

_SCRIPT = Path("/srv/autophagy-agent-current/automation/deploy-skill.sh")


def _record(root: Path, record_name: str, message_id: str) -> None:
    directory = root / "pending"
    directory.mkdir(parents=True, exist_ok=True)
    _ = (directory / f"{record_name}.json").write_text(
        json.dumps({"message_id": message_id, "hash": "a" * 64}), encoding="utf-8"
    )


class _Runner:
    def __init__(self, *codes: int) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._codes = list(codes)

    def __call__(self, command: tuple[str, ...]) -> int:
        self.calls.append(command)
        return self._codes[min(len(self.calls) - 1, len(self._codes) - 1)]


def _decide(answer: str):
    def decide(_request: object) -> str:
        return answer

    return decide


def _reconcile(verdict: str, reason: str = "checked"):
    """Stand in for the reconciler so a tick is verifiable without a gate or a store."""

    def reconcile(_request: object) -> Reconciled:
        return Reconciled(verdict, reason)

    return reconcile


_RUN = _reconcile(RUN)


def _outcomes(results) -> list[str]:
    return [result.outcome for result in results]


def test_an_approved_request_runs_the_pipeline_once(tmp_path: Path) -> None:
    _record(tmp_path, "demo", "1")
    runner = _Runner(0)
    results = watch_tick(tmp_path, _SCRIPT, decide=_decide("approved"), run=runner, reconcile=_RUN)
    assert _outcomes(results) == ["done"]
    assert runner.calls == [("sudo", "-n", str(_SCRIPT), "--skill", "demo")]


def test_a_settled_request_never_reaches_the_runner(tmp_path: Path) -> None:
    """Shelling out for a deploy the owner refused is what settled exists to prevent."""
    _record(tmp_path, "demo", "1")
    runner = _Runner(0)
    results = watch_tick(tmp_path, _SCRIPT, decide=_decide("denied"), run=runner, reconcile=_RUN)
    assert _outcomes(results) == ["settled"]
    assert runner.calls == []


def test_an_unanswered_request_never_reaches_the_runner(tmp_path: Path) -> None:
    _record(tmp_path, "demo", "1")
    runner = _Runner(0)
    results = watch_tick(tmp_path, _SCRIPT, decide=_decide("absent"), run=runner, reconcile=_RUN)
    assert _outcomes(results) == ["retain"]
    assert runner.calls == []


def test_lease_contention_is_reported_as_retry(tmp_path: Path) -> None:
    _record(tmp_path, "demo", "1")
    runner = _Runner(8)
    results = watch_tick(tmp_path, _SCRIPT, decide=_decide("approved"), run=runner, reconcile=_RUN)
    assert _outcomes(results) == ["retry"]


def test_a_cancellation_seen_by_the_pipeline_is_settled(tmp_path: Path) -> None:
    """The owner can react between the decision and the run; the pipeline re-checks."""
    _record(tmp_path, "demo", "1")
    runner = _Runner(9)
    results = watch_tick(tmp_path, _SCRIPT, decide=_decide("approved"), run=runner, reconcile=_RUN)
    assert _outcomes(results) == ["settled"]


def test_each_request_runs_exactly_once(tmp_path: Path) -> None:
    """Twice would race the pipeline's own per-skill execution lock against itself."""
    for name in ("alpha", "beta"):
        _record(tmp_path, name, "1")
    runner = _Runner(0)
    results = watch_tick(tmp_path, _SCRIPT, decide=_decide("approved"), run=runner, reconcile=_RUN)
    assert len(results) == 2
    assert [command[-1] for command in runner.calls] == ["alpha", "beta"]


def test_one_broken_record_does_not_starve_the_others(tmp_path: Path) -> None:
    """A raised exception aborting the tick would silently starve every other approval."""
    directory = tmp_path / "pending"
    directory.mkdir(parents=True)
    _ = (directory / "aaa-broken.json").write_text("{not json", encoding="utf-8")
    _record(tmp_path, "zzz-good", "1")
    runner = _Runner(0)
    results = watch_tick(tmp_path, _SCRIPT, decide=_decide("approved"), run=runner, reconcile=_RUN)
    assert _outcomes(results) == ["retain", "done"]
    assert [command[-1] for command in runner.calls] == ["zzz-good"]


def test_a_runner_failure_is_reported_not_raised(tmp_path: Path) -> None:
    _record(tmp_path, "demo", "1")

    def explode(_command: tuple[str, ...]) -> int:
        raise OSError("no such file")

    results = watch_tick(
        tmp_path, _SCRIPT, decide=_decide("approved"), run=explode, reconcile=_RUN
    )
    assert _outcomes(results) == ["failed"]


def test_a_runner_failure_reason_identifies_the_exit_code(tmp_path: Path) -> None:
    _record(tmp_path, "demo", "1")

    (result,) = watch_tick(
        tmp_path,
        _SCRIPT,
        decide=_decide("approved"),
        run=_Runner(126),
        reconcile=_RUN,
    )

    assert result.reason == "resume-exit:126"


def test_an_empty_directory_is_an_empty_tick(tmp_path: Path) -> None:
    assert (
        watch_tick(tmp_path, _SCRIPT, decide=_decide("approved"), run=_Runner(0), reconcile=_RUN)
        .requests
        == ()
    )


def test_each_result_carries_the_request_it_describes(tmp_path: Path) -> None:
    _record(tmp_path, "demo", "1")
    (result,) = watch_tick(
        tmp_path, _SCRIPT, decide=_decide("approved"), run=_Runner(0), reconcile=_RUN
    )
    assert result.request.name == "demo"
    assert result.reason == "owner-approved"


def test_an_already_realized_approval_is_retired_not_rerun(tmp_path: Path) -> None:
    """Re-running a mounted deploy buys nothing and spends the rate limit new approvals need."""
    _record(tmp_path, "demo", "1")
    runner = _Runner(0)
    results = watch_tick(
        tmp_path,
        _SCRIPT,
        decide=_decide("approved"),
        run=runner,
        reconcile=_reconcile(RETIRE_DONE, "already-realized"),
    )
    assert _outcomes(results) == [RETIRE_DONE]
    assert runner.calls == []


def test_deleted_message_during_reconcile_is_terminal_not_retry(tmp_path: Path) -> None:
    _record(tmp_path, "demo", "1")

    results = watch_tick(
        tmp_path,
        _SCRIPT,
        decide=_decide("approved"),
        run=_Runner(0),
        reconcile=_reconcile(SETTLED, "approval-message-missing"),
    )

    assert _outcomes(results) == [SETTLED]


def test_an_unfinished_approval_still_runs_the_pipeline(tmp_path: Path) -> None:
    """Reconciling must never become a way for a real approval to never reach the pipeline."""
    _record(tmp_path, "demo", "1")
    runner = _Runner(0)
    results = watch_tick(
        tmp_path, _SCRIPT, decide=_decide("approved"), run=runner, reconcile=_reconcile(RUN)
    )
    assert _outcomes(results) == ["done"]
    assert runner.calls == [("sudo", "-n", str(_SCRIPT), "--skill", "demo")]


def test_an_unresolvable_state_retains_instead_of_running(tmp_path: Path) -> None:
    """A store or an approval we cannot read is not permission — it costs one more tick."""
    _record(tmp_path, "demo", "1")
    runner = _Runner(0)
    results = watch_tick(
        tmp_path,
        _SCRIPT,
        decide=_decide("approved"),
        run=runner,
        reconcile=_reconcile(HOLD, "approval-unreadable"),
    )
    assert _outcomes(results) == ["retain"]
    assert runner.calls == []


def test_a_raising_reconciler_retains_instead_of_running(tmp_path: Path) -> None:
    """A reconciler that blew up answered nothing; running anyway would be a guess."""
    _record(tmp_path, "demo", "1")
    runner = _Runner(0)

    def explode(_request: object) -> Reconciled:
        raise OSError("gate unreachable")

    results = watch_tick(
        tmp_path, _SCRIPT, decide=_decide("approved"), run=runner, reconcile=explode
    )
    assert _outcomes(results) == ["retain"]
    assert runner.calls == []


def test_a_settled_request_is_never_reconciled(tmp_path: Path) -> None:
    """Reconciling probes Discord; doing it for a refusal spends a round trip on a closed case."""
    _record(tmp_path, "demo", "1")
    seen: list[object] = []

    def record_call(request: object) -> Reconciled:
        seen.append(request)
        return Reconciled(RUN, "unused")

    results = watch_tick(
        tmp_path, _SCRIPT, decide=_decide("denied"), run=_Runner(0), reconcile=record_call
    )
    assert _outcomes(results) == ["settled"]
    assert seen == []


def test_new_deterministic_failure_alerts_once_then_uses_fixed_grace() -> None:
    result = watch_tick_result("failed", "resume-exit:126")

    first = update_failure(None, result, release_sha="release-a", now=100.0)
    second = update_failure(first.state, result, release_sha="release-a", now=3700.0)

    assert first.alert is True
    assert first.state == FailureAttempt(first.state.fingerprint, 1, 3700.0)
    assert second.alert is False
    assert second.state.next_attempt_at == 7300.0


def test_transient_failure_uses_capped_exponential_backoff() -> None:
    result = watch_tick_result("retry", "resume-exit:8")
    state = None
    delays: list[float] = []

    for now in (0.0, 120.0, 360.0, 840.0, 1800.0, 3720.0):
        decision = update_failure(state, result, release_sha="release-a", now=now)
        state = decision.state
        delays.append(state.next_attempt_at - now)

    assert delays == [120.0, 240.0, 480.0, 960.0, 1920.0, 3600.0]


def test_changed_release_or_reason_is_a_new_failure_and_alerts_once() -> None:
    old = update_failure(
        None,
        watch_tick_result("failed", "resume-exit:1"),
        release_sha="release-a",
        now=0.0,
    )

    changed_reason = update_failure(
        old.state,
        watch_tick_result("failed", "resume-exit:126"),
        release_sha="release-a",
        now=1.0,
    )
    changed_release = update_failure(
        changed_reason.state,
        watch_tick_result("failed", "resume-exit:126"),
        release_sha="release-b",
        now=2.0,
    )

    assert changed_reason.alert is True
    assert changed_reason.state.attempts == 1
    assert changed_release.alert is True
    assert changed_release.state.attempts == 1


def test_active_backoff_skips_the_pipeline_without_becoming_a_new_failure(
    tmp_path: Path,
) -> None:
    _record(tmp_path, "demo", "1")
    runner = _Runner(0)

    results = watch_tick(
        tmp_path,
        _SCRIPT,
        decide=_decide("approved"),
        run=runner,
        reconcile=_RUN,
        eligible=lambda _request: False,
    )

    assert _outcomes(results) == ["backoff"]
    assert runner.calls == []


def test_a_release_change_ends_the_suppression_early() -> None:
    """A new release is evidence the cause was fixed, so the grace period has no more to buy.

    2026-08-04: three approvals failed under one release, the archive defect behind them was
    fixed in the next, and every tick for the following hour skipped them without a word. The
    fingerprint already named the release; only the clock was ever consulted.
    """
    first = update_failure(
        None,
        watch_tick_result("failed", "resume-exit:5"),
        release_sha="release-a",
        now=100.0,
    )

    assert retry_due(first.state, release_sha="release-a", now=200.0) is False
    assert retry_due(first.state, release_sha="release-b", now=200.0) is True
    assert retry_due(first.state, release_sha="release-a", now=3700.0) is True
    assert retry_due(None, release_sha="release-a", now=0.0) is True


def watch_tick_result(outcome: str, reason: str) -> TickResult:
    request = PendingRequest("skill-deploy:demo", "skill-deploy", "demo", "demo")
    return TickResult(request, outcome, reason)
