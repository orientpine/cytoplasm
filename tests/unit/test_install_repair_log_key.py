"""Issue #235: fresh installs must prepare the repair CLI's private-log transport."""
from __future__ import annotations

import functools
import os
import pwd as account_db
import stat
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from automation.install.apply import SystemMutator
from automation.install.assets import describe_action
from automation.install.plan import Check, EnableTimer, EnsureRepairLogKey, InstallInputs, SystemState, build_plan
from automation.install.repair_log_key import ensure_repair_log_key, inspect_repair_log_key
from automation.install import state as state_module
from automation.node_config import default_node_config


def test_issue_235_fresh_install_plans_repair_log_transport(tmp_path: Path) -> None:
    config = replace(
        default_node_config(),
        agent_home=tmp_path / "agent",
        ops_home=tmp_path / "ops",
    )
    inputs = InstallInputs(config, (), ("autophagy-deploy-reconcile.timer",))

    plan = build_plan(inputs, SystemState.empty())

    descriptions = [describe_action(action) for action in plan.actions]
    assert any("repair-log-key" in line for line in descriptions), descriptions
    repair = next(action for action in plan.actions if isinstance(action, EnsureRepairLogKey))
    assert repair.private_path == config.agent_home / ".ssh/autophagy-repair-log"
    assert repair.authorized_path == config.ops_home / ".ssh/authorized_keys"
    assert repair.receiver == config.release_current / "automation/repair/repair_private_log.py"
    assert all(
        plan.actions.index(repair) < index
        for index, action in enumerate(plan.actions) if isinstance(action, EnableTimer)
    )
    repeated = build_plan(inputs, SystemState.from_actions(plan.actions))
    assert all(isinstance(action, Check) for action in repeated.actions)


@functools.cache
def _public(seed: int) -> str:
    """One throwaway ed25519 public line per seed, made at test time; its private half is never kept."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / f"k{seed}"
        _ = subprocess.run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path), "-C", "fixture"),
                           check=True, capture_output=True)
        return path.with_suffix(".pub").read_text(encoding="utf-8").strip()


@pytest.fixture
def repair(tmp_path: Path) -> EnsureRepairLogKey:
    account = account_db.getpwuid(os.getuid()).pw_name
    host_public = tmp_path / "host.pub"
    _ = host_public.write_text(_public(2) + "\n", encoding="utf-8")
    return EnsureRepairLogKey(
        account, tmp_path / "agent", account, tmp_path / "ops",
        tmp_path / "current/automation/repair/repair_private_log.py", host_public,
    )


class FakeKeygen:
    def __init__(self, action: EnsureRepairLogKey) -> None:
        self.action = action
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self, command: tuple[str, ...], *,
        env: dict[str, str] | None = None, cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert env is None and cwd is None
        assert command[:9] == (
            "runuser", "-u", self.action.agent_account, "--", "ssh-keygen",
            "-q", "-t", "ed25519", "-N",
        )
        assert command[9:12] == ("", "-f", str(self.action.private_path))
        self.calls.append(command)
        _ = self.action.private_path.write_text("x\n", encoding="utf-8")
        _ = self.action.private_path.with_suffix(".pub").write_text(_public(1) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")


def test_apply_provisions_restricted_transport_and_preserves_other_lines(
    repair: EnsureRepairLogKey, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repair.authorized_path.parent.mkdir(parents=True)
    foreign = "# existing operator binding without newline"
    _ = repair.authorized_path.write_text(foreign, encoding="utf-8")
    repair.known_hosts_path.parent.mkdir(parents=True)
    known = "example.invalid " + _public(3) + "\n"
    _ = repair.known_hosts_path.write_text(known, encoding="utf-8")
    runner = FakeKeygen(repair)
    mutator = SystemMutator(default_node_config())
    monkeypatch.setattr(mutator, "run", runner)

    mutator.apply(repair)

    assert repair.authorized_path.read_text(encoding="utf-8") == (
        foreign + "\n" + f'restrict,command="/usr/bin/python3 -I {repair.receiver}" {_public(1)}\n'
    )
    assert repair.known_hosts_path.read_text(encoding="utf-8") == known + f"127.0.0.1 {_public(2)}\n"
    assert inspect_repair_log_key(repair)
    for path, mode in (
        (repair.private_path.parent, 0o700), (repair.authorized_path.parent, 0o700),
        (repair.private_path, 0o600), (repair.private_path.with_suffix(".pub"), 0o644),
        (repair.authorized_path, 0o600), (repair.known_hosts_path, 0o600),
    ):
        metadata = path.stat()
        assert stat.S_IMODE(metadata.st_mode) == mode
        assert (metadata.st_uid, metadata.st_gid) == (os.getuid(), os.getgid())


def test_second_apply_keeps_pair_and_bindings_unchanged(repair: EnsureRepairLogKey) -> None:
    runner = FakeKeygen(repair)
    ensure_repair_log_key(repair, runner)
    paths = (repair.private_path, repair.private_path.with_suffix(".pub"),
             repair.authorized_path, repair.known_hosts_path)
    original = tuple(path.read_bytes() for path in paths)

    ensure_repair_log_key(repair, runner)

    assert tuple(path.read_bytes() for path in paths) == original
    assert len(runner.calls) == 1


@pytest.mark.parametrize("survivor", ["private", "public"])
def test_partial_pair_refuses_regeneration(repair: EnsureRepairLogKey, survivor: str) -> None:
    repair.private_path.parent.mkdir(parents=True)
    path = repair.private_path if survivor == "private" else repair.private_path.with_suffix(".pub")
    _ = path.write_text("synthetic survivor\n", encoding="utf-8")
    runner = FakeKeygen(repair)

    with pytest.raises(OSError, match="partial; refusing regeneration"):
        ensure_repair_log_key(repair, runner)

    assert path.read_text(encoding="utf-8") == "synthetic survivor\n"
    assert runner.calls == []


@pytest.mark.parametrize("missing", ["private", "public", "authorized", "known", "host"])
def test_inspection_replans_missing_transport_assets(repair: EnsureRepairLogKey, missing: str) -> None:
    ensure_repair_log_key(repair, FakeKeygen(repair))
    paths = {
        "private": repair.private_path, "public": repair.private_path.with_suffix(".pub"),
        "authorized": repair.authorized_path, "known": repair.known_hosts_path,
        "host": repair.host_public_path,
    }
    paths[missing].unlink()

    ready = inspect_repair_log_key(repair)

    assert not ready
    inputs = InstallInputs(default_node_config(), (), ())
    converged = SystemState.from_actions(build_plan(inputs, SystemState.empty()).actions)
    replanned = build_plan(inputs, replace(converged, repair_log_ready=ready))
    assert len([action for action in replanned.actions if isinstance(action, EnsureRepairLogKey)]) == 1


@pytest.mark.parametrize("changed", ["binding", "host", "mode", "owner"])
def test_inspection_rejects_transport_drift(
    repair: EnsureRepairLogKey, changed: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ensure_repair_log_key(repair, FakeKeygen(repair))
    match changed:
        case "binding":
            _ = repair.authorized_path.write_text(_public(1) + "\n", encoding="utf-8")
        case "host":
            _ = repair.host_public_path.write_text(_public(4) + "\n", encoding="utf-8")
        case "mode":
            repair.private_path.chmod(0o644)
        case "owner":
            account = account_db.getpwnam(repair.agent_account)
            fields = list(account)
            fields[2] += 1  # pw_uid: the same account entry, owned by someone else
            foreign = type(account)(fields)
            monkeypatch.setattr(account_db, "getpwnam", lambda _: foreign)
        case _:
            pytest.fail("unknown drift fixture")

    assert not inspect_repair_log_key(repair)


@pytest.mark.parametrize("keep_correct", [False, True])
def test_conflicting_binding_refuses_to_add_another_authorization(
    repair: EnsureRepairLogKey, keep_correct: bool,
) -> None:
    runner = FakeKeygen(repair)
    ensure_repair_log_key(repair, runner)
    correct = repair.authorized_path.read_text(encoding="utf-8") if keep_correct else ""
    conflict = correct + _public(1) + "\n"
    _ = repair.authorized_path.write_text(conflict, encoding="utf-8")

    with pytest.raises(OSError, match="conflicting authorized_keys binding"):
        ensure_repair_log_key(repair, runner)

    assert repair.authorized_path.read_text(encoding="utf-8") == conflict
    assert len(runner.calls) == 1


def test_missing_host_public_key_stops_before_key_generation(repair: EnsureRepairLogKey) -> None:
    repair.host_public_path.unlink()
    runner = FakeKeygen(repair)

    with pytest.raises(OSError):
        ensure_repair_log_key(repair, runner)

    assert runner.calls == []
    assert not repair.private_path.exists()


def test_live_collector_reports_transport_convergence(
    repair: EnsureRepairLogKey, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ensure_repair_log_key(repair, FakeKeygen(repair))
    config = replace(
        default_node_config(), agent_account=repair.agent_account, agent_home=repair.agent_home,
        ops_account=repair.ops_account, ops_home=repair.ops_home,
        peer_home=tmp_path / "peer", deploy_checkout=tmp_path / "checkout",
        repair_work=tmp_path / "work", release_current=repair.receiver.parents[2],
    )
    monkeypatch.setattr(
        state_module, "inspect_repair_log_key",
        lambda action: inspect_repair_log_key(replace(action, host_public_path=repair.host_public_path)),
    )
    monkeypatch.setattr(
        state_module.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", ""),
    )

    state = state_module.inspect_state(InstallInputs(config, (), ()))

    assert state.repair_log_ready
    plan = build_plan(InstallInputs(config, (), ()), state)
    assert not any(isinstance(action, EnsureRepairLogKey) for action in plan.actions)
