from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK_HELPER = ROOT / "automation" / "deploy_execution_lock.py"
DEPLOY = ROOT / "automation" / "deploy-skill.sh"


def _lock_command() -> list[str]:
    return [sys.executable, str(LOCK_HELPER), "--skill", "wiki"]


def test_execution_lock_when_two_deploys_overlap_then_only_one_enters_critical_section(
    tmp_path: Path,
) -> None:
    # Given: one deploy holds the shared skill execution lease until its stdin closes.
    env = {**os.environ, "HOME": str(tmp_path)}
    with subprocess.Popen(
        _lock_command(),
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as first:
        assert first.stdout is not None
        assert first.stdout.readline().strip() == "EXECUTION-LOCK-ACQUIRED skill=wiki"

        # When: a simultaneous deploy tries to enter the same refresh-to-mount section.
        second = subprocess.run(
            _lock_command(),
            cwd=ROOT,
            env=env,
            input="",
            capture_output=True,
            text=True,
            check=False,
        )

        # Then: it fails before peer refresh or mount while the first remains active.
        assert second.returncode != 0
        assert "EXECUTION-LOCK-HELD skill=wiki" in second.stderr
        assert first.poll() is None
        assert first.stdin is not None
        first.stdin.close()
        assert first.wait(timeout=5) == 0


def test_execution_lock_when_holder_exits_then_next_deploy_can_enter(tmp_path: Path) -> None:
    # Given: no execution currently holds the crash-safe kernel lease.
    env = {**os.environ, "HOME": str(tmp_path)}

    # When: a deploy acquires the lease and immediately closes its input at clean exit.
    completed = subprocess.run(
        _lock_command(),
        cwd=ROOT,
        env=env,
        input="",
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: acquisition succeeds; a prior crash cannot leave a durable claim wedge.
    assert completed.returncode == 0
    assert completed.stdout == "EXECUTION-LOCK-ACQUIRED skill=wiki\n"


def test_deploy_holds_execution_lock_across_refresh_mount_and_consume() -> None:
    # Given: the production deploy script is the only mount orchestrator.
    script = DEPLOY.read_text(encoding="utf-8")

    # When: its critical-section markers and operations are ordered.
    acquire = script.index("\nstart_execution_lock\n")
    initial_attest = script.index('peer_attest "$SKILL" "$DIGEST"', acquire)
    mount = script.index("stage 4/4 MOUNT")
    consume = script.index('gate "" consume --skill "$SKILL"')
    release = script.index("\nrelease_execution_lock\n")

    # Then: one lease excludes a second execution from both refresh and deployment.
    assert acquire < initial_attest < mount < consume < release


def test_deploy_refresh_branch_reuses_binding_and_never_requests_owner_approval() -> None:
    # Given: peer refresh is a dedicated branch after an owner-bound gate check.
    script = DEPLOY.read_text(encoding="utf-8")
    start = script.index("check_with_attestation_refresh() {")
    body = script[start : script.index("\n}", start)]

    # When / Then: only exit 7 enables --refresh, using the same four binding arguments.
    assert '[[ "$approved" == 7 ]]' in body
    assert (
        'peer_attest "$SKILL" "$DIGEST" "$MESSAGE_ID" "$DEPLOY_NONCE" '
        '"$DEPLOY_APPROVALS_CHANNEL_ID" --refresh'
    ) in body
    assert "request --skill" not in body
