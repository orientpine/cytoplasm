"""RC-3/4 shell boundary: execute → gateway pair restart → full re-probe → receipt.

``test_deploy_all_plan`` fixes the pure judgment, but a green planner cannot catch the
orchestrator accidentally skipping the final probe or restarting only one Hermes account.
This file drives the real shell entrypoint with a stateful SSH stand-in; no node or deploy
mutation is involved.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_COMMAND: Final = _REPO / "automation" / "deploy_all.sh"
_EXAMPLE_CONFIG: Final = _REPO / "configs" / "node.example.toml"


def _stub_ssh(tmp_path: Path) -> tuple[Path, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls.log"
    receipt = tmp_path / "receipt.json"
    ssh = fake_bin / "ssh"
    _ = ssh.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        'cmd="${*: -1}"\n'
        'printf "%s\\n" "$cmd" >> "$FAKE_CALLS"\n'
        'if [[ "$cmd" == readlink* ]]; then\n'
        '  n="$(cat "$FAKE_READLINK_COUNTER" 2>/dev/null || printf 0)"\n'
        '  n=$((n + 1))\n'
        '  printf "%s" "$n" > "$FAKE_READLINK_COUNTER"\n'
        '  if (( n <= FAKE_STALE_UNTIL )); then\n'
        '    printf "/srv/releases/stale-sha\\n"\n'
        '  else\n'
        '    printf "/srv/releases/%s\\n" "$FAKE_HEAD"\n'
        '  fi\n'
        '  exit 0\n'
        "fi\n"
        'if [[ "$cmd" == *"--format actions"* ]]; then\n'
        '  printf "ACT|restart-gateway|agent+peer\\n"; exit 1\n'
        "fi\n"
        'if [[ "$cmd" == *"systemctl --user restart"* ]]; then\n'
        '  printf "active\\n"; exit 0\n'
        "fi\n"
        'if [[ "$cmd" == *"--format report"* ]]; then\n'
        '  printf "DEPLOY-ALL: clean\\n"; exit 0\n'
        "fi\n"
        'if [[ "$cmd" == *"--format receipt"* ]]; then\n'
        '  printf \'{"release_sha":"%s"}\\n\' "$FAKE_HEAD"; exit 0\n'
        "fi\n"
        'if [[ "$cmd" == *"cat >"*"/receipt.json"* ]]; then\n'
        '  cat > "$FAKE_RECEIPT"; exit 0\n'
        "fi\n"
        'if [[ "$cmd" == *"sha256sum"*"/receipt.json"* ]]; then\n'
        '  sha256sum "$FAKE_RECEIPT" | cut -d" " -f1; exit 0\n'
        "fi\n"
        'printf "unexpected ssh command: %s\\n" "$cmd" >&2\n'
        "exit 97\n",
        encoding="utf-8",
    )
    ssh.chmod(0o755)
    return fake_bin, calls, receipt


def _run(
    tmp_path: Path,
    *arguments: str,
    stale_until: int = 0,
    converge_seconds: str = "600",
    poll_seconds: str = "0",
) -> subprocess.CompletedProcess[str]:
    fake_bin, calls, receipt = _stub_ssh(tmp_path)
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=_REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "DEPLOY_SSH_HOST": "fake-node",
        "DEPLOY_ALL_RECEIPT_DIR": str(tmp_path / "private" / "deploy-all"),
        "FAKE_CALLS": str(calls),
        "FAKE_RECEIPT": str(receipt),
        "FAKE_HEAD": head,
        "FAKE_READLINK_COUNTER": str(tmp_path / "readlink-counter"),
        "FAKE_STALE_UNTIL": str(stale_until),
        "DEPLOY_ALL_CONVERGE_SECONDS": converge_seconds,
        "DEPLOY_ALL_CONVERGE_POLL_SECONDS": poll_seconds,
        "HEALTHCHECK_NODE_CONFIG_PATH": str(_EXAMPLE_CONFIG),
    }
    return subprocess.run(
        ("bash", str(_COMMAND), *arguments),
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_apply_restarts_both_gateways_then_reprobes_and_writes_receipt(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, "--apply")

    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert calls.count("systemctl --user restart") == 2
    assert "sudo -n -u agent" in calls
    assert "sudo -n -u peer" in calls
    assert calls.index("--format report") < calls.index("--format receipt")
    assert (tmp_path / "receipt.json").read_text(encoding="utf-8").startswith(
        '{"release_sha":'
    )


def test_apply_waits_for_the_node_release_then_deploys(tmp_path: Path) -> None:
    result = _run(tmp_path, "--apply", "--wait-converge", stale_until=2)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert calls.count("readlink ") >= 3


def test_apply_wait_timeout_uses_the_release_mismatch_exit(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        "--apply",
        "--wait-converge",
        stale_until=999,
        converge_seconds="0",
    )

    assert result.returncode == 4
    assert "RELEASE-MISMATCH" in result.stderr
    assert "timed out" in result.stderr


def test_plain_apply_rejects_a_stale_release_immediately(tmp_path: Path) -> None:
    result = _run(tmp_path, "--apply", stale_until=999)

    assert result.returncode == 4
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert calls.count("readlink ") == 1


def test_apply_rejects_an_unknown_extra_argument(tmp_path: Path) -> None:
    result = _run(tmp_path, "--apply", "--bogus")

    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert not (tmp_path / "calls.log").exists()


def test_unknown_mode_is_a_usage_error_without_contacting_the_node(
    tmp_path: Path,
) -> None:
    result = _run(tmp_path, "--unknown")

    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert not (tmp_path / "calls.log").exists()


def test_help_succeeds_without_contacting_the_node(tmp_path: Path) -> None:
    result = _run(tmp_path, "--help")

    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert not (tmp_path / "calls.log").exists()


def test_command_ships_executable() -> None:
    assert os.access(_COMMAND, os.X_OK)
