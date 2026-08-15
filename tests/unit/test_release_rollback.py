from __future__ import annotations

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
        smoke_script=current / "automation" / "deploy-smoke.sh",
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
    assert sum("deploy-smoke.sh" in command[-1] for command in fake.calls) == 1
    assert len(fake.notices) == 1
    failed = load_failed_release(runtime.failed_state)
    assert failed is not None
    assert failed.failed_sha == _TARGET_SHA
    assert failed.prior_sha == _PRIOR_SHA
    assert failed.phase is FailedReleasePhase.FAILED
    assert failed.notice_sent is True


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
    assert not any("deploy-smoke.sh" in command[-1] for command in fake.calls)
    assert sum(command[-1] == "health" for command in fake.calls) == 1
    assert len(fake.notices) == 1
