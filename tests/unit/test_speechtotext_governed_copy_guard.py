"""speechtotext CLI의 배포 사본 실행 경계를 고정한다."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation import skill_mount

ROOT = Path(__file__).parents[2]
CLI = ROOT / "skills/speechtotext/scripts/speechtotext_cli.py"
SCRIPTS = ROOT / "skills/speechtotext/scripts"


def _run(cli: Path, live_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "AUTOPHAGY_REPO_ROOT": str(ROOT),
           "AUTOPHAGY_SKILL_LIVE_ROOT": str(live_root)}
    return subprocess.run([sys.executable, str(cli), *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, check=False)


def test_mutating_repo_copy_is_blocked(tmp_path: Path) -> None:
    stale = tmp_path / "stale"
    stale.mkdir()
    for source in SCRIPTS.iterdir():
        if source.is_file():
            shutil.copy2(source, stale / source.name)
    live = tmp_path / "live/speechtotext/scripts"
    live.mkdir(parents=True)
    result = _run(stale / CLI.name, tmp_path / "live", "transcribe", "--file", "missing.wav")
    assert result.returncode == 3
    assert skill_mount.STALE_COPY_MARKER in result.stderr


def test_missing_live_skill_does_not_fire_guard(tmp_path: Path) -> None:
    stale = tmp_path / "stale"
    stale.mkdir()
    for source in SCRIPTS.iterdir():
        if source.is_file():
            shutil.copy2(source, stale / source.name)
    result = _run(stale / CLI.name, tmp_path / "live", "transcribe", "--file", "missing.wav")
    assert skill_mount.STALE_COPY_MARKER not in result.stderr
    assert result.returncode != 3


def test_constants_match_shared_definition() -> None:
    path = SCRIPTS / "speechtotext_governed.py"
    spec = importlib.util.spec_from_file_location("speechtotext_governed_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.GOVERNED_LIVE_ROOT == skill_mount.LIVE_ROOT
    assert module.LIVE_ROOT_ENV == skill_mount.LIVE_ROOT_ENV
    assert module.STALE_COPY_MARKER == skill_mount.STALE_COPY_MARKER
