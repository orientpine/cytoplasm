"""The node-side reconciler is the AUTHORITY for "origin/main is what prod runs".

MD-2. A CI runner is a low-latency trigger, not a guarantee: if it is offline, or the
workflow is deleted, or a label changes, a merge would silently do nothing — which is
precisely the drift this repo has been fighting (prod once sat 11 commits behind with
nobody the wiser). So a node-side timer independently reconciles, and the loud part is
that it tells the owner when it cannot.

The state machine is pure and injected: the tick takes the two shas, a clock, a
converge callable and a delivery callable, and returns the next state. That is what
makes "exactly one DM per incident" testable at all — the counting is the contract.
"""
from __future__ import annotations

from pathlib import Path

from automation.deploy_reconcile import FAILED_RELEASE_RC, ReconcileState, reconcile_tick

_MODULE = Path(__file__).resolve().parents[2] / "automation" / "deploy_reconcile.py"

_A = "a" * 40
_B = "b" * 40
_C = "c" * 40


class _Converge:
    def __init__(self, *codes: int) -> None:
        self._codes: list[int] = list(codes)
        self.calls: int = 0

    def __call__(self) -> int:
        self.calls += 1
        return self._codes[min(self.calls - 1, len(self._codes) - 1)]


class _Deliver:
    def __init__(self, *, fails: int = 0) -> None:
        self.sent: list[str] = []
        self._fails: int = fails

    def __call__(self, text: str) -> bool:
        if self._fails > 0:
            self._fails -= 1
            return False
        self.sent.append(text)
        return True


def _run(
    *, origin: str, current: str, converge: _Converge, deliver: _Deliver,
    ticks: int, start: float = 0.0, period: float = 120.0,
    state: ReconcileState | None = None,
) -> ReconcileState:
    now = start
    current_state = state or ReconcileState()
    for _ in range(ticks):
        current_state = reconcile_tick(
            current_state, origin_sha=origin, current_sha=current, now=now,
            converge=converge, deliver=deliver,
        )
        now += period
    return current_state


def test_no_drift_is_silent() -> None:
    converge, deliver = _Converge(0), _Deliver()
    state = _run(origin=_A, current=_A, converge=converge, deliver=deliver, ticks=3)
    assert converge.calls == 0
    assert deliver.sent == []
    assert state == ReconcileState()


def test_drift_converges_once() -> None:
    converge, deliver = _Converge(0), _Deliver()
    _ = _run(origin=_B, current=_A, converge=converge, deliver=deliver, ticks=1)
    assert converge.calls == 1
    assert deliver.sent == [], "a successful convergence is silent"


def test_rc5_is_transient() -> None:
    """SYNC-BLOCK means another convergence holds the lock — not a failure."""
    converge, deliver = _Converge(5), _Deliver()
    state = _run(origin=_B, current=_A, converge=converge, deliver=deliver, ticks=3)
    assert converge.calls == 3
    assert deliver.sent == []
    assert state.consecutive_failures == 0


def test_a_rollback_that_never_finishes_notifies_once_at_threshold() -> None:
    converge, deliver = _Converge(FAILED_RELEASE_RC), _Deliver()
    state = _run(origin=_B, current=_A, converge=converge, deliver=deliver, ticks=6)
    assert converge.calls == 6
    assert len(deliver.sent) == 1
    assert state.notified_target == f"rollback:{_B}"
    assert state.incident_open is True


def test_three_failures_notify_once() -> None:
    converge, deliver = _Converge(1), _Deliver()
    state = _run(origin=_B, current=_A, converge=converge, deliver=deliver, ticks=6)
    assert len(deliver.sent) == 1, deliver.sent
    assert state.notified_target == _B


def test_drift_over_ten_minutes_notifies() -> None:
    """rc=0 every time, yet `current` never advances: converging is not converged."""
    converge, deliver = _Converge(0), _Deliver()
    _ = _run(origin=_B, current=_A, converge=converge, deliver=deliver, ticks=8, period=120.0)
    assert len(deliver.sent) == 1


def test_discord_failure_preserves_state_and_resends_once() -> None:
    converge, deliver = _Converge(1), _Deliver(fails=1)
    state = _run(origin=_B, current=_A, converge=converge, deliver=deliver, ticks=3)
    assert deliver.sent == []
    assert state.pending_notice is not None
    state = _run(
        origin=_B, current=_A, converge=converge, deliver=deliver, ticks=3, state=state,
    )
    assert len(deliver.sent) == 1, "queued notice is delivered exactly once"
    assert state.pending_notice is None


def test_recovery_notifies_once_and_closes() -> None:
    converge, deliver = _Converge(1), _Deliver()
    state = _run(origin=_B, current=_A, converge=converge, deliver=deliver, ticks=3)
    assert len(deliver.sent) == 1
    state = _run(
        origin=_B, current=_B, converge=converge, deliver=deliver, ticks=3, state=state,
    )
    assert len(deliver.sent) == 2, "one recovery notice, then silence"
    assert state == ReconcileState()


def test_never_touches_prod_state() -> None:
    """The reconciler converges or complains. It never repairs by hand."""
    text = _MODULE.read_text(encoding="utf-8")
    for forbidden in ("pull --ff-only", "systemctl", "symlink", "rmtree", "unlink"):
        assert forbidden not in text, forbidden


def test_the_unit_never_lets_python_write_bytecode_into_the_sealed_release() -> None:
    """이 유닛은 릴리스에서 python 을 돌린다 — 캐시를 남기면 그 릴리스가 배포를 막는다.

    2026-08-17 실측: 리컨실러가 `automation/__pycache__/{__init__,node_config}.cpython-312.pyc`
    를 릴리스 트리에 떨어뜨렸고, release-provenance 가 "커밋에 없는 파일"로 판정해
    `RELEASE-STORE-BLOCK` 을 냈다. 승인이 끝난 배포 2건이 마운트 직전에서 멈췄다.

    같은 이유로 `deploy-skill.sh:50` 과 `autophagy-resume-deploy` 는 이미 이 변수를 켠다.
    릴리스에서 python 을 실행하는 경로는 예외 없이 이것을 켜야 한다.
    """
    unit = (
        Path(__file__).resolve().parents[2]
        / "automation" / "systemd" / "autophagy-deploy-reconcile.service"
    ).read_text(encoding="utf-8")
    assert "Environment=PYTHONDONTWRITEBYTECODE=1" in unit
