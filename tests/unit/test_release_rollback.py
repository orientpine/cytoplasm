from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from automation.deploy_reconcile import FAILED_RELEASE_RC
from automation.release_rollback import (
    Command,
    FailedReleasePhase,
    ReleaseEffects,
    ReleaseRuntime,
    ReleaseTransition,
    apply_release_update,
    load_failed_release,
)
from automation.release_store import activate_release, rollback_current
from automation.update_trust_state import advance_release_floor, load_release_floor


_PRIOR_SHA = "a" * 40
_TARGET_SHA = "b" * 40


def _release(root: Path, sha: str) -> Path:
    release = root / "autophagy-agent-releases" / sha
    release.mkdir(parents=True)
    _ = (release / ".origin-sha").write_text(f"{sha}\n", encoding="utf-8")
    return release


def _runtime(tmp_path: Path) -> ReleaseRuntime:
    store_root = tmp_path / "srv"
    prior = _release(store_root, _PRIOR_SHA)
    _ = _release(store_root, _TARGET_SHA)
    current = store_root / "autophagy-agent-current"
    activate_release(current, prior)
    return ReleaseRuntime(
        current=current,
        store_root=store_root,
        failed_state=store_root / "autophagy-private" / "deploy-reconcile" / "failed-release.json",
        release_helper=tmp_path / "libexec" / "autophagy-install-release",
        gateway_helper=tmp_path / "libexec" / "autophagy-gateway-pair",
        smoke_script=current / "automation" / "release-smoke.sh",
    )


@dataclass(slots=True)  # noqa: MUTABLE_OK - the fake records effect calls
class _FakeCommands:
    runtime: ReleaseRuntime
    restart_codes: list[int] = field(default_factory=lambda: [0])
    smoke_code: int = 0
    health_code: int = 0
    calls: list[Command] = field(default_factory=list)
    converge_calls: int = 0
    notices: list[str] = field(default_factory=list)

    def converge(self) -> int:
        self.converge_calls += 1
        target = self.runtime.store_root / "autophagy-agent-releases" / _TARGET_SHA
        activate_release(self.runtime.current, target)
        return 0

    def run(self, command: Command, _timeout: float) -> int:
        self.calls.append(command)
        if command[-1] == "restart":
            return self.restart_codes.pop(0)
        if command[-1] == "health":
            return self.health_code
        if "rollback" in command:
            _ = rollback_current(
                Path(command[command.index("--store-root") + 1]),
                failed_sha=command[command.index("--failed-sha") + 1],
                prior_sha=command[command.index("--sha") + 1],
            )
            return 0
        return self.smoke_code

    def notify(self, notice: str) -> bool:
        self.notices.append(notice)
        return True

    def effects(self) -> ReleaseEffects:
        return ReleaseEffects(converge=self.converge, run=self.run, notify=self.notify)


def _apply(runtime: ReleaseRuntime, fake: _FakeCommands) -> int:
    return apply_release_update(
        ReleaseTransition(prior_sha=_PRIOR_SHA, target_sha=_TARGET_SHA),
        runtime,
        fake.effects(),
    )


def test_success_restarts_the_gateway_pair_then_smokes_and_keeps_current(tmp_path: Path) -> None:
    # Given
    runtime = _runtime(tmp_path)
    fake = _FakeCommands(runtime)

    # When
    result = _apply(runtime, fake)

    # Then
    assert result == 0
    assert runtime.current.resolve().name == _TARGET_SHA
    assert fake.calls == [
        ("sudo", "-n", str(runtime.gateway_helper), "restart"),
        ("/usr/bin/bash", str(runtime.smoke_script)),
    ]
    assert fake.notices == []


def test_smoke_failure_rolls_back_once_and_three_ticks_do_not_flap(tmp_path: Path) -> None:
    # Given
    runtime = _runtime(tmp_path)
    fake = _FakeCommands(runtime, restart_codes=[0, 0], smoke_code=23)

    # When
    first = _apply(runtime, fake)
    later = tuple(_apply(runtime, fake) for _ in range(3))

    # Then
    assert first == FAILED_RELEASE_RC
    assert later == (FAILED_RELEASE_RC,) * 3
    assert runtime.current.resolve().name == _PRIOR_SHA
    assert fake.converge_calls == 1
    assert sum(command[-1] == "restart" for command in fake.calls) == 2
    assert sum("release-smoke.sh" in command[-1] for command in fake.calls) == 1
    assert len(fake.notices) == 1
    failed = load_failed_release(runtime.failed_state)
    assert failed is not None
    assert failed.failed_sha == _TARGET_SHA
    assert failed.prior_sha == _PRIOR_SHA
    assert failed.phase is FailedReleasePhase.FAILED
    assert failed.notice_sent is True


def test_intentional_runtime_rollback_does_not_lower_the_verified_release_floor(
    tmp_path: Path,
) -> None:
    # Given: signature verification already advanced the installation-wide floor.
    floor = tmp_path / "private" / "deploy-reconcile" / "release-floor.json"
    advance_release_floor(floor, "v2.0.0", _TARGET_SHA)
    runtime = _runtime(tmp_path)
    fake = _FakeCommands(runtime, restart_codes=[0, 0], smoke_code=23)

    # When: smoke failure intentionally rolls the runtime pointer back.
    result = _apply(runtime, fake)

    # Then: runtime recovery cannot reopen the signed update channel to older tags.
    assert result == FAILED_RELEASE_RC
    assert runtime.current.resolve().name == _PRIOR_SHA
    pinned = load_release_floor(floor)
    assert pinned is not None
    assert (pinned.tag, pinned.commit_sha) == ("v2.0.0", _TARGET_SHA)


def test_one_gateway_restart_failure_uses_the_same_rollback_path(tmp_path: Path) -> None:
    # Given: the pair helper reports that one member failed, then rollback restart succeeds.
    runtime = _runtime(tmp_path)
    fake = _FakeCommands(runtime, restart_codes=[1, 0])

    # When
    result = _apply(runtime, fake)

    # Then
    assert result == FAILED_RELEASE_RC
    assert runtime.current.resolve().name == _PRIOR_SHA
    assert sum(command[-1] == "restart" for command in fake.calls) == 2
    assert not any("release-smoke.sh" in command[-1] for command in fake.calls)
    assert sum(command[-1] == "health" for command in fake.calls) == 1
    assert len(fake.notices) == 1


_STALE_FAILED_SHA = "c" * 40
_STALE_PRIOR_SHA = "d" * 40


def _write_fingerprint(runtime: ReleaseRuntime, failed_sha: str, prior_sha: str) -> None:
    """Plant the exact 6-field fingerprint shape the node carries, ROLLBACK_PENDING."""
    runtime.failed_state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = runtime.failed_state.write_text(
        json.dumps(
            {
                "version": 1,
                "failed_sha": failed_sha,
                "prior_sha": prior_sha,
                "reason": "gateway-restart",
                "phase": FailedReleasePhase.ROLLBACK_PENDING.value,
                "notice_sent": True,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_a_stale_rollback_fingerprint_does_not_block_convergence(tmp_path: Path) -> None:
    # Given: a rollback-pending fingerprint whose two shas are BOTH strangers to the live
    # pointer — the shape the node carried for three days after humans landed newer releases
    # out-of-band (2026-08-16 incident). Its rollback can never complete: the pointer is
    # neither the failed generation to undo nor the prior one to return to.
    runtime = _runtime(tmp_path)
    _write_fingerprint(runtime, failed_sha=_STALE_FAILED_SHA, prior_sha=_STALE_PRIOR_SHA)
    fake = _FakeCommands(runtime)

    # When
    result = _apply(runtime, fake)

    # Then: the stale record yields and the tick converges normally.
    assert result == 0
    assert fake.converge_calls == 1
    assert runtime.current.resolve().name == _TARGET_SHA
    assert not runtime.failed_state.exists()
    # The owner already heard about that rollback; recovery is reconcile_tick's to announce.
    assert fake.notices == []


def test_a_stale_fingerprint_naming_the_target_still_blocks_retry(tmp_path: Path) -> None:
    # Given: same stranded shape, but the fingerprint names the very sha we are about to
    # install. Suppressing a known-bad generation is the one duty it still carries.
    runtime = _runtime(tmp_path)
    _write_fingerprint(runtime, failed_sha=_TARGET_SHA, prior_sha=_STALE_PRIOR_SHA)
    fake = _FakeCommands(runtime)

    # When
    results = tuple(_apply(runtime, fake) for _ in range(3))

    # Then: still refused, still never converged — but no longer rewriting ROLLBACK_PENDING
    # forever, so a later sha is not blocked by this record.
    assert results == (FAILED_RELEASE_RC,) * 3
    assert fake.converge_calls == 0
    assert runtime.current.resolve().name == _PRIOR_SHA
    assert len(fake.notices) <= 1
    failed = load_failed_release(runtime.failed_state)
    assert failed is not None
    assert failed.phase is FailedReleasePhase.FAILED
