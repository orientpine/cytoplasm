"""report 스킬의 배포 사본 실행 경계를 고정한다."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPTS = ROOT / "skills/report/scripts"
CLI = SCRIPTS / "report_cli.py"


def _load_governed():
    path = SCRIPTS / "report_governed.py"
    spec = importlib.util.spec_from_file_location("report_governed_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(cli: Path, live_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "AUTOPHAGY_REPO_ROOT": str(ROOT),
        "AUTOPHAGY_SKILL_LIVE_ROOT": str(live_root),
    }
    return subprocess.run(
        [sys.executable, str(cli), *args], cwd=ROOT, env=env,
        capture_output=True, text=True, check=False,
    )


def _stale_copy(tmp_path: Path) -> Path:
    stale = tmp_path / "stale/report/scripts"
    stale.mkdir(parents=True)
    for source in SCRIPTS.iterdir():
        if source.is_file():
            shutil.copy2(source, stale / source.name)
    return stale


def test_governed_constants_match_shared_definition() -> None:
    from automation import skill_mount

    governed = _load_governed()
    assert governed.GOVERNED_LIVE_ROOT == skill_mount.LIVE_ROOT
    assert governed.LIVE_ROOT_ENV == skill_mount.LIVE_ROOT_ENV
    assert governed.SKILL_NAME == "report"
    assert governed.STALE_COPY_MARKER == skill_mount.STALE_COPY_MARKER


def test_mutating_stale_copy_is_refused(tmp_path: Path) -> None:
    stale = _stale_copy(tmp_path)
    live = tmp_path / "live"
    live_scripts = live / "report/scripts"
    live_scripts.mkdir(parents=True)
    result = _run(stale / "report_cli.py", live, "slides", "--report", str(tmp_path / "missing.md"))
    assert result.returncode == 3
    assert "STALE-SKILL-COPY-BLOCK" in result.stderr


def test_missing_live_skill_does_not_refuse(tmp_path: Path) -> None:
    stale = _stale_copy(tmp_path)
    result = _run(stale / "report_cli.py", tmp_path / "live", "slides", "--report", str(tmp_path / "missing.md"))
    assert "STALE-SKILL-COPY-BLOCK" not in result.stderr
