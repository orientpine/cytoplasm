from __future__ import annotations

import argparse
import re
import threading
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Callable, TypeAlias

import pytest

from automation import (
    peer_attest,
    peer_attestation,
    skill_gate,
    skill_gate_refresh,
    skill_gate_specs,
)
from automation.deploy_execution_lock import LOCK_HELD_EXIT
from automation.interop.approval_lease import FileKeyLease
from tests.unit.approval_lifecycle_security_harness import (
    MESSAGE_ID,
    OWNER_ID,
    SKILL,
    LifecycleHarness,
    approval_event_count,
    check_args,
    execute_deploy,
    install_harness,
    peer_request,
)

Invalidator: TypeAlias = Callable[[LifecycleHarness], argparse.Namespace]


@dataclass(frozen=True, slots=True)
class BoundaryCase:
    elapsed: timedelta
    refresh_posts: int


def _rewrite_pending(harness: LifecycleHarness, field: str, value: str) -> None:
    content = harness.pending.read_text(encoding="utf-8")
    pattern = re.compile(rf'("{re.escape(field)}":\s*)"[^"]*"')
    updated, count = pattern.subn(rf'\1"{value}"', content, count=1)
    assert count == 1
    _ = harness.pending.write_text(updated, encoding="utf-8")


def _change_content_hash(harness: LifecycleHarness) -> argparse.Namespace:
    return check_args(harness, "0" * 64)


def _reuse_nonce(harness: LifecycleHarness) -> argparse.Namespace:
    _ = harness.pending.with_name("other-skill.json").write_text(
        harness.pending.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return check_args(harness)


def _change_action(harness: LifecycleHarness) -> argparse.Namespace:
    _rewrite_pending(harness, "approval_action", "skill.publish")
    return check_args(harness)


def _change_destination(harness: LifecycleHarness) -> argparse.Namespace:
    _rewrite_pending(harness, "approval_destination", "skill:other")
    return check_args(harness)


def _cancel_request(harness: LifecycleHarness) -> argparse.Namespace:
    harness.discord.reactions[(MESSAGE_ID, skill_gate_specs.CANCEL_EMOJI)] = [OWNER_ID]
    return check_args(harness)


def _supersede_request(harness: LifecycleHarness) -> argparse.Namespace:
    _rewrite_pending(harness, "message_id", "request-2")
    return check_args(harness)


def _withdraw_owner_approval(harness: LifecycleHarness) -> argparse.Namespace:
    harness.discord.reactions[(MESSAGE_ID, skill_gate_specs.APPROVE_EMOJI)] = []
    return check_args(harness)


def test_single_owner_approval_when_peer_attestation_expires_then_refreshes_and_deploys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one owner reaction and one exact peer verdict for the persisted execution binding.
    harness = install_harness(tmp_path, monkeypatch)
    pending_before = harness.pending.read_text(encoding="utf-8")
    harness.clock.advance(peer_attestation.PEER_ATTESTATION_TTL + timedelta(minutes=15))
    args = check_args(harness)

    # When: execution detects peer-only expiry, refreshes the same binding, then checks again.
    assert skill_gate.cmd_check(args) == 1
    refresh_decision = skill_gate_refresh.refresh_required(args)
    assert refresh_decision == skill_gate_refresh.PEER_ATTESTATION_REFRESH_EXIT
    assert approval_event_count(harness) == 0
    assert harness.discord.owner_request_posts == 1
    refreshed = peer_attest.attest(
        replace(peer_request(harness), refresh=True),
        harness.discord,
        now=harness.clock.current,
    )
    assert refreshed.exit_code == 0
    assert approval_event_count(harness) == 0
    decision = skill_gate.cmd_check(args)
    if decision == 0:
        harness.discord.mounts += 1

    # Then: no owner prompt or decision was created by refresh; one final bound event deploys once.
    assert decision == 0
    assert harness.pending.read_text(encoding="utf-8") == pending_before
    assert harness.discord.owner_request_posts == 1
    assert harness.discord.reactions[
        (MESSAGE_ID, skill_gate_specs.APPROVE_EMOJI)
    ] == [OWNER_ID]
    assert harness.discord.peer_refresh_posts == 1
    assert harness.discord.mounts == 1
    assert approval_event_count(harness) == 1


@pytest.mark.parametrize(
    "boundary",
    (
        BoundaryCase(peer_attestation.PEER_ATTESTATION_TTL, 0),
        BoundaryCase(
            peer_attestation.PEER_ATTESTATION_TTL + timedelta(microseconds=1), 1
        ),
    ),
    ids=("exact-ttl-is-valid", "past-ttl-refreshes"),
)
def test_peer_attestation_ttl_boundary_when_deploy_executes_then_is_inclusive(
    boundary: BoundaryCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one approved binding whose initial peer verdict has a deterministic age.
    harness = install_harness(tmp_path, monkeypatch)
    harness.clock.advance(boundary.elapsed)

    # When: the complete deploy lifecycle evaluates the boundary.
    decision = execute_deploy(harness, check_args(harness))

    # Then: exact TTL remains valid, while the first later instant refreshes before one mount.
    assert decision == 0
    assert harness.discord.peer_refresh_posts == boundary.refresh_posts
    assert harness.discord.mounts == 1
    assert approval_event_count(harness) == 1


@pytest.mark.parametrize(
    "invalidate",
    (
        _change_content_hash,
        _reuse_nonce,
        _change_action,
        _change_destination,
        _cancel_request,
        _supersede_request,
        _withdraw_owner_approval,
    ),
    ids=(
        "content-hash-change",
        "nonce-reuse",
        "action-change",
        "destination-change",
        "request-cancelled",
        "request-superseded",
        "owner-withdrawal",
    ),
)
def test_existing_owner_approval_when_binding_or_decision_changes_then_never_executes(
    invalidate: Invalidator,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an expired peer verdict and one required owner/binding invariant broken.
    harness = install_harness(tmp_path, monkeypatch)
    harness.clock.advance(peer_attestation.PEER_ATTESTATION_TTL + timedelta(minutes=15))
    args = invalidate(harness)

    # When: the deployment attempts the ordinary check and automatic refresh path.
    decision = execute_deploy(harness, args)

    # Then: the existing approval is never reused for refresh, an event, or deployment.
    assert decision == 1
    assert harness.discord.owner_request_posts == 1
    assert harness.discord.peer_refresh_posts == 0
    assert harness.discord.mounts == 0
    assert approval_event_count(harness) == 0


def test_concurrent_execution_when_peer_is_expired_then_refreshes_and_deploys_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two overlapping executions contend for the production execution lease key.
    harness = install_harness(tmp_path, monkeypatch)
    harness.clock.advance(peer_attestation.PEER_ATTESTATION_TTL + timedelta(minutes=15))
    lease = FileKeyLease(tmp_path / "execution-leases")
    key = f"skill-deploy-execution:{SKILL}"
    entered, release = threading.Event(), threading.Event()
    first_results: list[int] = []
    second_results: list[int] = []

    def first_execution() -> None:
        with lease.hold(key) as owned:
            entered.set()
            if not release.wait(timeout=5):
                first_results.append(-1)
                return
            first_results.append(
                execute_deploy(harness, check_args(harness)) if owned else LOCK_HELD_EXIT
            )

    def second_execution() -> None:
        with lease.hold(key) as owned:
            second_results.append(
                execute_deploy(harness, check_args(harness)) if owned else LOCK_HELD_EXIT
            )

    # When: the second execution arrives while the first still owns refresh-through-mount.
    first = threading.Thread(target=first_execution)
    second = threading.Thread(target=second_execution)
    first.start()
    assert entered.wait(timeout=5)
    second.start()
    second.join(timeout=5)
    release.set()
    first.join(timeout=5)

    # Then: one contender is refused and exactly one refresh, event, and mount survives.
    assert not first.is_alive()
    assert not second.is_alive()
    assert first_results == [0]
    assert second_results == [LOCK_HELD_EXIT]
    assert harness.discord.peer_refresh_posts == 1
    assert harness.discord.mounts == 1
    assert approval_event_count(harness) == 1
