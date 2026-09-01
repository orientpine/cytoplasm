"""Release approval must execute where the agent token and gate ledger live.

The workstation owns the signing key, but it deliberately has no Discord bot
credential.  A fake SSH boundary proves that the committed automation snapshot
is staged remotely and that plan bytes reach the agent-side producer on stdin.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_CLIENT = _REPO / "automation" / "release_approval_remote.sh"


def test_request_stages_origin_main_and_runs_with_agent_secrets(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text('{"head":"abc"}\n', encoding="utf-8")
    calls = tmp_path / "calls"
    payloads = tmp_path / "payloads"
    fake_ssh = tmp_path / "ssh"
    fake_ssh.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$CALLS"
case "$*" in
  *"test -f"*) exit 1 ;;
  *"release_approval"*"request"*)
    cat >> "$PAYLOADS"
    printf '%s\n' '{"message_id":"m1"}'
    ;;
  *) cat >/dev/null ;;
esac
""",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o755)

    result = subprocess.run(
        (
            "bash",
            str(_CLIENT),
            "request",
            "--plan-file",
            str(plan),
        ),
        cwd=_REPO,
        env={
            **os.environ,
            "RELEASE_APPROVAL_SSH": str(fake_ssh),
            "RELEASE_APPROVAL_HOST": "primary",
            "RELEASE_APPROVAL_ACCOUNT": "agent",
            "RELEASE_APPROVAL_HOME": "/home/agent",
            "CALLS": str(calls),
            "PAYLOADS": str(payloads),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    invoked = calls.read_text(encoding="utf-8")
    normalized = invoked.replace("\\", "").replace("'", "")
    assert "sudo -n -u agent -H" in invoked
    assert ". /home/agent/.env.secrets" in normalized
    assert "mktemp" in normalized
    assert "cat >" in normalized
    assert "python3 -m automation.release_approval request --plan-file" in normalized
    assert "/dev/stdin" not in normalized
    assert "DISCORD_BOT_TOKEN" not in os.environ
    sent = payloads.read_text(encoding="utf-8")
    assert '{"head":"abc"}' in sent


def test_release_defaults_to_the_remote_agent_client() -> None:
    source = (_REPO / "automation" / "release.sh").read_text(encoding="utf-8")
    assert 'approval=("$SCRIPT_DIR/release_approval_remote.sh")' in source


def test_release_builds_the_repo_plan_on_the_workstation() -> None:
    source = (_REPO / "automation" / "release.sh").read_text(encoding="utf-8")

    assert "plan_approval=(python3 -m automation.release_approval)" in source
    assert '"${plan_approval[@]}" plan --repo "$REPO_ROOT"' in source

