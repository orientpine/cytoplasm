"""Deploying any skill ff-pulls the ops library under every ALREADY-live skill.

AS-3.2 proved that moves the shared library out from under three live approval
flows at once — an unrelated deploy breaks them. ``deploy-skill.sh`` now scans the
live fleet against the freshly-pulled library right after the sync, and WARNs
rather than dies: blocking here would strand the owner approval this very deploy
already consumed, which is worse than the fail-closed refuse-to-post the skew
itself causes. The scan is exercised by sourcing the script and calling
``scan_live_skill_abi`` with a stubbed ``run_as`` — no node, no network.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DEPLOY = _REPO / "automation" / "deploy-skill.sh"


def _scan_function(tmp_path: Path) -> Path:
    """Extract just scan_live_skill_abi — the script's whole top level runs on source."""
    text = _DEPLOY.read_text(encoding="utf-8")
    start = text.index("scan_live_skill_abi() {")
    end = text.index("\n}", start) + len("\n}")
    out = tmp_path / "scan_fn.sh"
    out.write_text(text[start:end] + "\n", encoding="utf-8")
    return out


def _run_scan(tmp_path: Path, *, checker_out: str, checker_rc: int, strict: bool = False) -> subprocess.CompletedProcess[str]:
    """Run scan_live_skill_abi in isolation with log/die/run_as stubbed."""
    journal = tmp_path / "run_as.log"
    preamble = (
        'set -uo pipefail\n'
        'log() { printf "[deploy-skill] %s\\n" "$*" >&2; }\n'
        'die() { log "ERROR: $1"; exit "${2:-4}"; }\n'
        'STORE_ROOT="/srv/autophagy-skills"\n'
        'NODE_OPS_ACCOUNT="ops"\n'
        'NODE_DEPLOY_CHECKOUT="/srv/autophagy-agents"\n'
        f'run_as() {{ printf "run_as %s\\n" "$*" >> "{journal}"; printf "%s" "$CHECKER_OUT"; return "$CHECKER_RC"; }}\n'
    )
    script = (
        f"{preamble}"
        f'source "{_scan_function(tmp_path)}"\n'
        "scan_live_skill_abi\n"
        'echo "SCAN-RC=$?"\n'
    )
    env = dict(os.environ)
    env["CHECKER_OUT"] = checker_out
    env["CHECKER_RC"] = str(checker_rc)
    env["DEPLOY_SSH_HOST"] = "example-primary-node-not-this-host"
    if strict:
        env["DEPLOY_ABI_STRICT"] = "1"
    return subprocess.run(
        ("bash", "-c", script), capture_output=True, text=True, check=False, env=env
    )


def test_a_clean_live_fleet_produces_no_warning(tmp_path: Path) -> None:
    result = _run_scan(tmp_path, checker_out="ABI-OK: 0 violation(s), 0 skipped", checker_rc=0)
    assert "SCAN-RC=0" in result.stdout
    assert "MOUNT-ABI-WARN" not in result.stderr


def test_an_abi_break_warns_but_the_deploy_proceeds(tmp_path: Path) -> None:
    result = _run_scan(
        tmp_path,
        checker_out="ABI-VIOLATION x_binding.py::directory DiscordChannelDirectory: unexpected keyword 'approval_env_var'",
        checker_rc=1,
    )
    assert "SCAN-RC=0" in result.stdout  # WARN never aborts the deploy
    assert "MOUNT-ABI-WARN" in result.stderr
    assert "approval_env_var" in result.stderr


def test_a_crashing_checker_warns_but_the_deploy_proceeds(tmp_path: Path) -> None:
    # A guard that breaks must never break a deploy.
    result = _run_scan(tmp_path, checker_out="Traceback (most recent call last): boom", checker_rc=2)
    assert "SCAN-RC=0" in result.stdout
    assert "MOUNT-ABI-WARN" in result.stderr


def test_strict_mode_escalates_a_break_to_a_block(tmp_path: Path) -> None:
    result = _run_scan(
        tmp_path,
        checker_out="ABI-VIOLATION x_binding.py::directory DiscordChannelDirectory: unexpected keyword 'approval_env_var'",
        checker_rc=1,
        strict=True,
    )
    assert "SCAN-RC=0" not in result.stdout  # die aborts before the echo
    assert "MOUNT-ABI-BLOCK" in result.stderr


def test_the_scan_runs_after_the_ff_pull_in_the_sync_function() -> None:
    """The scan must see the library the pull just moved, so it comes after it.

    DG-4 added a release-current branch that converges a snapshot then scans and
    returns early; the historical ff-pull path still scans after its pull. Check
    the ordering inside that fallback path (from the ff-pull to the end)."""
    script = _DEPLOY.read_text(encoding="utf-8")
    sync_start = script.index("sync_ops_checkout_for_peer_attest() {")
    sync_end = script.index("\n}", sync_start)
    body = script[sync_start:sync_end]
    assert "git -C $NODE_DEPLOY_CHECKOUT pull --ff-only" in body
    # the ff-pull fallback path scans after its pull
    fallback = body[body.index("pull --ff-only"):]
    assert "scan_live_skill_abi" in fallback
    assert "skill_library_abi.py" in script
