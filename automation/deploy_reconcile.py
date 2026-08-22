"""Node-side reconciliation: origin/main is what prod runs, or the owner hears about it.

MD-2. A CI runner is a low-latency trigger, never a guarantee — it can be offline, its
workflow can be deleted, its label can change. Any of those turns "merge a PR" back
into silence, which is the exact failure this repo keeps paying for (prod once sat 11
commits behind origin with nobody the wiser). So the node reconciles independently and
the timer, not GitHub, is the authority.

The half that matters is the complaining. A reconciler that retries forever in silence
is the same silence with extra steps. Hence the state machine here, whose contract is
counting: **one** owner notice per incident, **one** recovery notice, and never a
notice for contention that is merely someone else converging right now.

Pure and injected on purpose — the two shas, a clock, a converge callable and a
delivery callable come from the caller. That is what makes "exactly one DM" assertable
instead of hopeful. Effects (reading the release pointer, calling the privileged
helper, talking to Discord) live in the cron wrapper; this module decides only.

What it must never do: repair prod by hand. It converges through the one privileged
helper or it says it could not. Fixing the release pointer, restarting a service, or
deleting a release are all how a well-meaning reconciler turns a stale runtime into a
broken one, and are pinned as forbidden by tests/unit/test_deploy_reconcile.py.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Final, TypeAlias

Converge: TypeAlias = Callable[[], int]
"""Runs the privileged helper. 0 converged · 5 someone else holds the lock · else failed."""

Deliver: TypeAlias = Callable[[str], bool]
"""Sends one owner notice. False means "not delivered" — the notice is then queued."""

#: Another convergence holds the host-global lock. Transient by construction: the very
#: contention proves a converger is running, so counting it as a failure would page the
#: owner about the system working.
LOCK_CONTENTION_RC: Final = 5

#: The target was activated, failed post-convergence validation, and was rolled back.
#: Its durable fingerprint suppresses another attempt until origin names a new sha.
FAILED_RELEASE_RC: Final = 6

#: Consecutive genuine failures before the owner is told.
FAILURE_NOTICE_THRESHOLD: Final = 3

#: A convergence can report success and still leave `current` behind (an install that
#: verified against the wrong generation, a helper that exited early). Elapsed drift is
#: therefore its own trigger: rc=0 forever is not evidence that prod moved.
DRIFT_NOTICE_SECONDS: Final = 600.0


@dataclass(frozen=True, slots=True)
class ReconcileState:
    """Survives ticks and reboots. The default value is the "nothing is wrong" state."""

    consecutive_failures: int = 0
    drift_since: float | None = None
    notified_target: str | None = None
    pending_notice: str | None = None
    incident_open: bool = False
    skip_reason: str | None = None


def _drift_notice(*, origin_sha: str, current_sha: str, failures: int, elapsed: float) -> str:
    return (
        "prod has not converged to origin/main.\n"
        f"  origin/main : {origin_sha}\n"
        f"  runtime     : {current_sha}\n"
        f"  실패 {failures}회 · 미수렴 {int(elapsed // 60)}분\n"
        "자동 재시도는 계속됩니다. 반복되면 노드에서 원인을 확인하세요 "
        "(재시작·포인터 수정은 하지 마세요)."
    )


def _recovery_notice(*, current_sha: str) -> str:
    return f"prod가 origin/main에 다시 도달했습니다: {current_sha}"


def reconcile_skip(
    state: ReconcileState,
    *,
    reason: str,
    now: float,
    deliver: Deliver,
) -> ReconcileState:
    """Count one structurally blocked tick and reuse the drift-notice lifecycle."""
    if state.pending_notice is not None and deliver(state.pending_notice):
        state = replace(state, pending_notice=None)

    same_reason = state.skip_reason == reason
    failures = state.consecutive_failures + 1 if same_reason else 1
    drift_since = (
        state.drift_since if same_reason and state.drift_since is not None else now
    )
    incident_key = f"skip:{reason}"
    state = replace(
        state,
        consecutive_failures=failures,
        drift_since=drift_since,
        skip_reason=reason,
    )
    if failures < FAILURE_NOTICE_THRESHOLD or state.notified_target == incident_key:
        return state

    notice = _drift_notice(
        origin_sha="unresolved",
        current_sha=f"blocked: {reason}",
        failures=failures,
        elapsed=now - drift_since,
    )
    delivered = deliver(notice)
    return replace(
        state,
        notified_target=incident_key,
        incident_open=True,
        pending_notice=None if delivered else notice,
    )


def reconcile_tick(
    state: ReconcileState,
    *,
    origin_sha: str,
    current_sha: str,
    now: float,
    converge: Converge,
    deliver: Deliver,
) -> ReconcileState:
    """One reconciliation tick. Returns the state to persist.

    ``current_sha`` is the VERIFIED runtime generation, not what a previous tick hoped
    to install — otherwise a convergence that reports success while prod stays behind
    would never be noticed, which is the whole failure mode being guarded.
    """
    # A queued notice is delivered before anything else: the incident it describes is
    # older than whatever this tick finds, and dropping it would lose the only signal.
    if state.pending_notice is not None and deliver(state.pending_notice):
        state = replace(state, pending_notice=None)

    if origin_sha == current_sha:
        if state.incident_open:
            recovery = _recovery_notice(current_sha=current_sha)
            if not deliver(recovery):
                return replace(state, pending_notice=recovery, incident_open=False)
        return ReconcileState()

    drift_since = state.drift_since if state.drift_since is not None else now
    rc = converge()

    if rc == FAILED_RELEASE_RC:
        reason = "rollback-pending"
        same_reason = state.skip_reason == reason
        failures = state.consecutive_failures + 1 if same_reason else 1
        rollback_since = (
            state.drift_since if same_reason and state.drift_since is not None else now
        )
        incident_key = f"rollback:{origin_sha}"
        state = replace(
            state,
            consecutive_failures=failures,
            drift_since=rollback_since,
            incident_open=True,
            skip_reason=reason,
        )
        if failures < FAILURE_NOTICE_THRESHOLD or state.notified_target == incident_key:
            return state
        notice = _drift_notice(
            origin_sha=origin_sha,
            current_sha=current_sha,
            failures=failures,
            elapsed=now - rollback_since,
        )
        delivered = deliver(notice)
        return replace(
            state,
            notified_target=incident_key,
            pending_notice=None if delivered else notice,
        )

    if state.skip_reason is not None:
        state = replace(
            state,
            consecutive_failures=0,
            drift_since=None,
            skip_reason=None,
        )
        drift_since = now

    if rc == 0 or rc == LOCK_CONTENTION_RC:
        failures = state.consecutive_failures
    else:
        failures = state.consecutive_failures + 1

    elapsed = now - drift_since
    should_notify = (
        failures >= FAILURE_NOTICE_THRESHOLD or elapsed >= DRIFT_NOTICE_SECONDS
    ) and state.notified_target != origin_sha

    state = replace(state, consecutive_failures=failures, drift_since=drift_since)
    if not should_notify:
        return state

    notice = _drift_notice(
        origin_sha=origin_sha, current_sha=current_sha, failures=failures, elapsed=elapsed
    )
    delivered = deliver(notice)
    # `notified_target` advances either way: the incident is now known, and rebuilding
    # the same notice next tick would page the owner once per tick instead of once.
    return replace(
        state,
        notified_target=origin_sha,
        incident_open=True,
        pending_notice=None if delivered else notice,
    )
