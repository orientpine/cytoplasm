"""procurement CLI의 낡은 사본 실행 차단을 고정한다."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation import skill_mount

ROOT = Path(__file__).parents[2]
CLI = ROOT / "skills/procurement/scripts/procure_cli.py"
SCRIPTS = ROOT / "skills/procurement/scripts"


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "AUTOPHAGY_REPO_ROOT": str(ROOT),
           "AUTOPHAGY_SKILL_LIVE_ROOT": str(root / "live")}
    return subprocess.run([sys.executable, str(CLI), *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, check=False)


def _stale_copy(tmp_path: Path) -> Path:
    stale = tmp_path / "stale/scripts"
    stale.mkdir(parents=True)
    for source in SCRIPTS.iterdir():
        if source.is_file():
            shutil.copy2(source, stale / source.name)
    return stale


def test_mutating_repo_copy_is_blocked(tmp_path: Path) -> None:
    _stale_copy(tmp_path)
    live = tmp_path / "live/procurement/scripts"
    live.mkdir(parents=True)
    result = _run(tmp_path, "review", "--file", "missing")
    assert result.returncode == 3
    assert skill_mount.STALE_COPY_MARKER in result.stderr


def test_missing_live_skill_does_not_fire_guard(tmp_path: Path) -> None:
    _stale_copy(tmp_path)
    result = _run(tmp_path, "review", "--file", "missing")
    assert result.returncode != 3 or skill_mount.STALE_COPY_MARKER not in result.stderr


def test_governed_module_constants_match_shared_definition() -> None:
    path = SCRIPTS / "procurement_governed.py"
    spec = importlib.util.spec_from_file_location("procurement_governed_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.GOVERNED_LIVE_ROOT == skill_mount.LIVE_ROOT
    assert module.LIVE_ROOT_ENV == skill_mount.LIVE_ROOT_ENV
    assert module.STALE_COPY_MARKER == skill_mount.STALE_COPY_MARKER
