from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_HEALTHCHECK = _REPO / "automation" / "healthcheck.sh"


def _run_probe(tmp_path: Path, capture_body: str) -> subprocess.CompletedProcess[str]:
    journal = tmp_path / "command.txt"
    script = (
        f'source "{_HEALTHCHECK}"; '
        f'capture_on_node() {{ printf "%s\\n" "$2" > "{journal}"; {capture_body}; }}; '
        "report_roster_identity"
    )
    env = dict(os.environ)
    env["HEALTHCHECK_SSH_USER"] = ""
    env["HEALTHCHECK_SSH_IDENTITY"] = ""
    return subprocess.run(
        ("bash", "-c", script),
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_report_roster_identity_when_valid_then_logs_identity_without_gating(
    tmp_path: Path,
) -> None:
    # Given
    result = _run_probe(
        tmp_path,
        "printf '%s' 'ROSTER-IDENTITY group_id=\"example-lab\" members=2'",
    )

    # When/Then
    assert result.returncode == 0
    assert result.stdout == '[healthcheck] INFO ROSTER-IDENTITY group_id="example-lab" members=2\n'
    command = (tmp_path / "command.txt").read_text(encoding="utf-8")
    assert "sudo -n -u agent -H" in command
    assert "python3 -m automation.group_roster identity /home/agent/.hermes/roster.yaml" in command


def test_report_roster_identity_when_absent_then_reports_unavailable_and_returns_zero(
    tmp_path: Path,
) -> None:
    # Given/When
    result = _run_probe(tmp_path, "return 1")

    # Then
    assert result.returncode == 0
    assert result.stdout == "[healthcheck] INFO ROSTER-UNAVAILABLE\n"


def test_report_roster_identity_when_output_is_invalid_then_does_not_fail_healthcheck(
    tmp_path: Path,
) -> None:
    # Given/When
    result = _run_probe(tmp_path, "printf '%s' 'ROSTER-INVALID: malformed'")

    # Then
    assert result.returncode == 0
    assert result.stdout == "[healthcheck] INFO ROSTER-UNAVAILABLE\n"
