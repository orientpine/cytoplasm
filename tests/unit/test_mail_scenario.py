from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
_SCENARIO = _REPO / "skills" / "mail" / "scripts" / "scenario.sh"


def test_mail_scenario_facade_probe_is_independent_of_caller_cwd(tmp_path: Path) -> None:
    """A release checkout cwd must not make a staged facade probe succeed."""
    source = _SCENARIO.read_text(encoding="utf-8")
    import_at = source.index("import automation.entity_preflight.gate")
    start = source.rfind("if ", 0, import_at) + len("if ")
    end = source.index(" 2>/dev/null; then", import_at)
    probe = source[start:end]
    staged_root = tmp_path / "staged"
    staged_root.mkdir()
    command = f'repo_root="$1"\n{probe}'

    checkout = subprocess.run(
        ["bash", "-c", command, "--", str(staged_root)],
        check=False,
        cwd=_REPO,
        capture_output=True,
        text=True,
    )
    elsewhere = subprocess.run(
        ["bash", "-c", command, "--", str(staged_root)],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert checkout.returncode == elsewhere.returncode, (
        "facade probe answer changed with caller cwd: "
        f"checkout={checkout.returncode}, elsewhere={elsewhere.returncode}"
    )
    assert probe.startswith(
        "python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]);"
    )


def test_mail_scenario_runs_with_staged_interop_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: an isolated runtime and a parent credential excluded from the allowlist.
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "DUMMY-parent-only")
    runtime = tmp_path / "runtime"
    _ = runtime.mkdir()
    _ = shutil.copytree(_REPO / "automation", runtime / "automation")
    environment = {
        "AUTOPHAGY_DEMO_SECRET": "DUMMY-mail-scenario",
        "AUTOPHAGY_REPO_ROOT": str(runtime),
        "HOME": str(tmp_path),
        "INTEROP_RUNTIME": str(runtime),
        "PATH": os.environ["PATH"],
    }

    # When: the mail scenario runs outside the source checkout.
    result = subprocess.run(
        ["bash", str(_SCENARIO)],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        env=environment,
        text=True,
    )

    # Then: the staged signed-confirm path passes without leaking the parent token.
    assert result.returncode == 0, result.stderr
    assert "SCENARIO-PASS" in result.stdout
    assert "leg=signed-confirm" in result.stdout
