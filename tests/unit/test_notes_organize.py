"""notes-weekly-organize no-agent cron wrapper contract (W5-3 obs fix).

Runs the repo wrapper as a real subprocess with a fake HOME so the tests pin
the exact cron surface: silent success, masked one-line failure detail, and
the missing-mount guidance. Background: 2026-07-20 the wrapper swallowed the
child's ModuleNotFoundError traceback and printed only "rc=1", making the
incident undiagnosable from the cron record.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_WRAPPER = _REPO / "automation" / "notes_organize" / "notes_organize.py"


def _run_wrapper(home: Path, scripts: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HOME": str(home)}
    if scripts is not None:
        env["REPORT_SCRIPTS"] = str(scripts)
    return subprocess.run(
        [sys.executable, str(_WRAPPER)],
        capture_output=True, text=True, timeout=60, check=False, env=env,
    )


def _plant_self_cli(home: Path, body: str) -> None:
    scripts = home / ".hermes" / "skills" / "report" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "report_cli.py").write_text(body, encoding="utf-8")


def _plant_cli_in(scripts: Path, body: str) -> None:
    scripts.mkdir(parents=True)
    (scripts / "report_cli.py").write_text(body, encoding="utf-8")


def test_child_failure_surfaces_masked_stderr_tail(tmp_path: Path) -> None:
    # Given: the mounted CLI dies with a traceback-style stderr tail
    scripts = tmp_path / "mounted-report" / "scripts"
    _plant_cli_in(
        scripts,
        "import sys\n"
        "sys.stderr.write('Traceback (most recent call last):\\n')\n"
        "sys.stderr.write(\"ModuleNotFoundError: No module named 'report' \"\n"
        "                 'mail victim@inst.example id 123456789\\n')\n"
        "sys.exit(3)\n",
    )
    # When: the cron wrapper runs
    result = _run_wrapper(tmp_path, scripts)
    # Then: one alert line carries rc + the masked last stderr line
    assert result.returncode == 1
    out = result.stdout.strip()
    assert out.startswith("notes-organize error rc=3: ")
    assert "ModuleNotFoundError: No module named 'report'" in out
    assert "[MASKED-EMAIL]" in out and "victim@inst.example" not in out
    assert "[MASKED-NUM]" in out and "123456789" not in out
    assert len(out) <= len("notes-organize error rc=3: ") + 300


def test_success_is_silent_exit_zero(tmp_path: Path) -> None:
    # Given: the mounted CLI succeeds
    scripts = tmp_path / "mounted-report" / "scripts"
    _plant_cli_in(scripts, "print('NOTES-ORGANIZED path=x')\n")
    # When/Then: no_agent success contract — empty stdout, exit 0
    result = _run_wrapper(tmp_path, scripts)
    assert result.returncode == 0
    assert result.stdout == ""


def test_missing_mount_keeps_guidance_line(tmp_path: Path) -> None:
    # Given: no report skill under this HOME
    result = _run_wrapper(tmp_path)
    # Then: the pre-existing guidance line is preserved
    assert result.returncode == 1
    assert result.stdout.strip() == "notes-organize error: report skill is not mounted"


def test_wrapper_ignores_the_self_skill_root(tmp_path: Path) -> None:
    # Given: a working lookalike exists only in the account-owned self-skill root.
    _plant_self_cli(tmp_path, "raise SystemExit(0)\n")

    # When: the wrapper resolves the governed report CLI.
    result = _run_wrapper(tmp_path)

    # Then: the self-authored skill is ignored rather than executed.
    assert result.returncode != 0
    assert result.stdout.strip() == "notes-organize error: report skill is not mounted"


def test_wrapper_honors_the_scripts_env_override(tmp_path: Path) -> None:
    # Given: the governed CLI is supplied through the test/runtime override.
    scripts = tmp_path / "mounted-report" / "scripts"
    _plant_cli_in(scripts, "raise SystemExit(0)\n")

    # When: the wrapper runs with that scripts directory.
    result = _run_wrapper(tmp_path, scripts)

    # Then: the mounted CLI runs successfully and the wrapper remains silent.
    assert result.returncode == 0
    assert result.stdout == ""
