"""One watcher tick, composed — enumerate, decide, and run only what was approved.

FA-3. The pieces each exist on their own: enumerate the pending records, plan what each
one means, build the single command a resume may run, read what the attempt meant. This
is the composition, and composition is where the remaining mistakes live — running the
wrong plan, running one twice, letting one bad record eat the rest of the tick.

Four guarantees, each earned the hard way earlier in this cycle:

* **Only an approved request runs anything.** A settled or retained plan never reaches
  the runner. Shelling out for a deploy the owner refused is exactly what the settled
  action was introduced to prevent.
* **Exactly once per request per tick.** Twice would race the pipeline's own per-skill
  execution lock against itself — the pipeline takes that lock, so a watcher that
  double-invokes deadlocks against its own second call.
* **One bad record does not starve the others.** A raised exception aborting the tick
  would leave every other approval unprocessed, silently, which is the failure mode this
  whole feature exists to remove. Unreadable records surface as ``retain`` through the
  planner and the tick keeps going.
* **An approval already realized is retired, not re-run.** The mount is what the owner's
  ✅ authorized, so once it happened the request's only remaining exit is the gate's own
  compare-and-swap. Re-invoking the pipeline changes nothing that is already mounted and
  spends the Discord rate limit a genuinely new approval needs; the reconciler is a
  required argument precisely so no caller can quietly drop that check.

The runner is injected. Nothing here spawns a process, so the flow is verifiable end to
end without privilege, and the privileged edge stays one small call at the caller.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from automation.supply_chain_decide import make_decider
from automation.supply_chain_effects import interpret_resume, resume_command
from automation.supply_chain_plan import RESUME, RETAIN, SETTLED, PendingRequest, Plan, plan_tick
from automation.supply_chain_reconcile import RETIRE_DONE, RUN, Reconciled
from automation.supply_chain_records import EnumerationResult, enumerate_pending

Runner = Callable[[tuple[str, ...]], int]
"""Runs the resume command and returns its exit code. May raise; that is a failure."""

DecisionOf = Callable[[str], str]
"""``approved`` | ``denied`` | ``absent`` for the message a record is bound to."""

Reconcile = Callable[[PendingRequest], Reconciled]
"""Whether an approved request still needs the pipeline, or was already realized."""


@dataclass(frozen=True, slots=True)
class TickResult:
    """What happened to one request this tick, and why."""

    request: PendingRequest
    outcome: str
    reason: str


@dataclass(frozen=True, slots=True)
class FailureAttempt:
    fingerprint: str
    attempts: int
    next_attempt_at: float


@dataclass(frozen=True, slots=True)
class FailureDecision:
    state: FailureAttempt
    alert: bool


def update_failure(
    previous: FailureAttempt | None,
    result: TickResult,
    *,
    release_sha: str,
    now: float,
) -> FailureDecision:
    fingerprint = f"{release_sha}:{result.reason}"
    attempts = previous.attempts + 1 if previous is not None and previous.fingerprint == fingerprint else 1
    transient = result.outcome == "retry"
    delay = min(120.0 * (2 ** (attempts - 1)), 3600.0) if transient else 3600.0
    state = FailureAttempt(fingerprint, attempts, now + delay)
    return FailureDecision(state, previous is None or previous.fingerprint != fingerprint)


def retry_due(previous: FailureAttempt | None, *, release_sha: str, now: float) -> bool:
    """Whether a suppressed request may try again — the clock OR a release change ends it.

    The fingerprint already names the release the failure happened under, so a different
    release is the strongest available signal the cause was fixed; waiting out the hour
    then is pure loss (2026-08-04: three approvals stalled until a human noticed).
    """
    if previous is None:
        return True
    if previous.fingerprint.split(":", 1)[0] != release_sha:
        return True
    return now >= previous.next_attempt_at


Eligible = Callable[[PendingRequest], bool]


def _always_eligible(_request: PendingRequest) -> bool:
    return True


def _execute(
    plan: Plan, deploy_script: Path, run: Runner, reconcile: Reconcile, eligible: Eligible
) -> TickResult:
    if plan.action != RESUME:
        return TickResult(plan.request, plan.action, plan.reason)
    if not eligible(plan.request):
        return TickResult(plan.request, "backoff", "backoff-active")
    try:
        settled = reconcile(plan.request)
    except Exception:  # noqa: BLE001 - a reconciler that blew up answered nothing, so retain
        return TickResult(plan.request, RETAIN, "reconcile-failed")
    if settled.verdict == RETIRE_DONE:
        return TickResult(plan.request, RETIRE_DONE, settled.reason)
    if settled.verdict == SETTLED:
        return TickResult(plan.request, SETTLED, settled.reason)
    if settled.verdict != RUN:
        return TickResult(plan.request, RETAIN, f"hold:{settled.reason}")
    try:
        code = run(resume_command(deploy_script, plan.request))
    except Exception:  # noqa: BLE001 - a runner that blew up is a failed attempt, not a crash
        return TickResult(plan.request, "failed", plan.reason)
    outcome = interpret_resume(code)
    reason = plan.reason if outcome == "done" else f"resume-exit:{code}"
    return TickResult(plan.request, outcome, reason)


def watch_tick(
    gate_dir: Path,
    deploy_script: Path,
    *,
    decide: DecisionOf,
    run: Runner,
    reconcile: Reconcile,
    eligible: Eligible = _always_eligible,
) -> EnumerationResult[TickResult]:
    """Process every live request once, in the enumerator's stable order.

    ``decide`` answers per MESSAGE, not per request: the record-reading step is assembled
    here so an unreadable record raises inside the planner and becomes ``retain`` — the
    caller cannot accidentally skip that check by supplying its own shortcut.

    ``reconcile`` is required, not defaulted, and runs ONLY for a plan that would resume.
    An approval whose mount already happened must be retired through the gate's own CAS
    rather than by re-invoking the pipeline: the rerun changes nothing that is already
    mounted, and it spends the rate limit a genuinely new approval needs. A default would
    let a caller silently reintroduce that rerun, so there is none. A settled or retained
    plan never reaches it — probing Discord about a decision the owner already closed is a
    round trip spent on nothing.
    """
    enumeration = enumerate_pending(gate_dir)
    decider = make_decider(gate_dir, decision_of=decide)
    return EnumerationResult(
        tuple(
            _execute(plan, deploy_script, run, reconcile, eligible)
            for plan in plan_tick(enumeration.requests, decide=decider)
        ),
        enumeration.succeeded,
    )
