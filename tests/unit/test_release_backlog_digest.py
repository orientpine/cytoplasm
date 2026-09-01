"""VA-3 release-backlog digest: an unsigned origin/main is normal; AGE is the signal.

New file rather than new cases in ``test_deploy_reconcile.py`` — that file is pinned by
an FS3 settlement record (task-10 replays one of its nodes at a fixed ref), and
tests/AGENTS.md forbids growing pinned files. The unsigned-head incident tests it used
to carry asserted the pre-VA semantics (one notice per unsigned sha, which under
merge=축적 would page the owner once per merge) and are replaced here by their digest
counterparts: silence before the three-day threshold, one digest per aged period with
commit count and age, episode continuity across head advances, and reset on release.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import automation.deploy_reconcile as reconcile
from automation.deploy_reconcile import ReconcileState, reconcile_tick
from automation.deploy_reconcile_unsigned import unreleased_commit_count
from automation.git_tag_signature import GitRunner

_A = "a" * 40
_B = "b" * 40
_C = "c" * 40
_DAY = 86400.0


class _Deliver:
    def __init__(self, *, fails: int = 0) -> None:
        self.sent: list[str] = []
        self._fails = fails

    def __call__(self, text: str) -> bool:
        if self._fails > 0:
            self._fails -= 1
            return False
        self.sent.append(text)
        return True


def _tick(
    state: ReconcileState,
    *,
    now: float,
    deliver: _Deliver,
    head: str = _B,
    count: int | None = 7,
) -> ReconcileState:
    return reconcile.reconcile_unsigned_head(
        state,
        remote_head=head,
        current_sha=_A,
        now=now,
        deliver=deliver,
        commit_count=count,
    )


def test_an_unsigned_head_is_silent_before_the_threshold() -> None:
    # Given/When: a young backlog is observed across two days of ticks.
    deliver = _Deliver()
    state = ReconcileState()
    for now in (0.0, _DAY, 2 * _DAY):
        state = _tick(state, now=now, deliver=deliver)

    # Then: normal state — counted, never paged.
    assert deliver.sent == []
    assert state.skip_reason == "release-backlog"
    assert state.incident_open is False
    assert state.consecutive_failures == 3


def test_the_digest_fires_once_past_three_days_with_count_and_age() -> None:
    deliver = _Deliver()
    state = ReconcileState()
    for now in (0.0, 3 * _DAY + 1.0, 3 * _DAY + 120.0):
        state = _tick(state, now=now, deliver=deliver)

    assert len(deliver.sent) == 1
    assert "7건" in deliver.sent[0]
    assert "3일" in deliver.sent[0]
    assert "automation/release.sh" in deliver.sent[0]
    assert _B in deliver.sent[0]
    assert _A in deliver.sent[0]
    assert state.incident_open is True


def test_a_new_head_does_not_reset_the_backlog_clock() -> None:
    """sha 별로 리셋하면 활발히 머지할수록 다이제스트가 영영 오지 않는다."""
    deliver = _Deliver()
    state = _tick(ReconcileState(), now=0.0, deliver=deliver)
    state = _tick(state, now=2 * _DAY, deliver=deliver, head=_C)
    state = _tick(state, now=3 * _DAY + 1.0, deliver=deliver, head=_C)

    assert len(deliver.sent) == 1
    assert _C in deliver.sent[0]


def test_the_digest_repeats_once_per_period_not_per_tick() -> None:
    deliver = _Deliver()
    state = ReconcileState()
    for now in (0.0, 3 * _DAY + 1.0, 4 * _DAY, 6 * _DAY + 1.0, 6 * _DAY + 120.0):
        state = _tick(state, now=now, deliver=deliver)

    assert len(deliver.sent) == 2
    assert "6일" in deliver.sent[1]


def test_a_failed_delivery_is_retried_from_the_pending_notice() -> None:
    deliver = _Deliver(fails=1)
    state = _tick(ReconcileState(), now=0.0, deliver=deliver)
    state = _tick(state, now=3 * _DAY + 1.0, deliver=deliver)
    assert state.pending_notice is not None

    state = _tick(state, now=3 * _DAY + 120.0, deliver=deliver)

    assert len(deliver.sent) == 1
    assert state.pending_notice is None


def test_an_unknown_commit_count_is_said_not_guessed() -> None:
    deliver = _Deliver()
    state = _tick(ReconcileState(), now=0.0, deliver=deliver, count=None)
    _ = _tick(state, now=3 * _DAY + 1.0, deliver=deliver, count=None)

    assert len(deliver.sent) == 1
    assert "수 미상" in deliver.sent[0]


def test_a_landed_release_closes_the_digest_incident_once() -> None:
    # Given: a digest was sent for an aged backlog.
    deliver = _Deliver()
    state = _tick(ReconcileState(), now=0.0, deliver=deliver)
    state = _tick(state, now=3 * _DAY + 1.0, deliver=deliver)
    assert len(deliver.sent) == 1

    # When: a signed release lands and runtime equals origin/main again.
    for now in (3 * _DAY + 240.0, 3 * _DAY + 360.0):
        state = reconcile_tick(
            state,
            origin_sha=_B,
            current_sha=_B,
            now=now,
            converge=lambda: 0,
            deliver=deliver,
        )

    # Then: one recovery notice and a clean episode reset.
    assert len(deliver.sent) == 2
    assert state == ReconcileState()


# --- unreleased_commit_count 관측자 --------------------------------------------------


def _runner(stdout: str, rc: int = 0, observed: list[list[str]] | None = None) -> GitRunner:
    def run(
        args: list[str],
        /,
        *,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env, capture_output, text, timeout
        if observed is not None:
            observed.append(args)
        return subprocess.CompletedProcess(args, rc, stdout=stdout, stderr="")

    return run


def test_commit_count_uses_only_a_read_only_rev_list(tmp_path: Path) -> None:
    observed: list[list[str]] = []

    count = unreleased_commit_count(tmp_path, _A, _B, _runner("12\n", observed=observed))

    assert count == 12
    assert observed == [
        ["git", "-C", str(tmp_path), "rev-list", "--count", f"{_A}..{_B}"]
    ]


def test_commit_count_degrades_to_none_instead_of_guessing(tmp_path: Path) -> None:
    # 미러가 origin 을 아직 못 따라온 상태(객체 부재)는 정상이다 — 수를 지어내지 않는다.
    assert unreleased_commit_count(tmp_path, _A, _B, _runner("", rc=128)) is None
    assert unreleased_commit_count(tmp_path, _A, _B, _runner("not-a-number\n")) is None
    assert unreleased_commit_count(tmp_path, "", _B, _runner("3\n")) is None
    assert unreleased_commit_count(tmp_path, _A, "junk", _runner("3\n")) is None
