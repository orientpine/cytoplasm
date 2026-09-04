"""budget CLI의 배포 사본 판정을 고정한다."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation import skill_mount

ROOT = Path(__file__).parents[2]
CLI = ROOT / "skills/budget/scripts/budget_cli.py"
SCRIPTS = ROOT / "skills/budget/scripts"


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "AUTOPHAGY_REPO_ROOT": str(ROOT),
           "AUTOPHAGY_SKILL_LIVE_ROOT": str(root / "live")}
    return subprocess.run(
        [sys.executable, str(CLI), *args], cwd=ROOT, env=env,
        capture_output=True, text=True, check=False,
    )


def test_mutating_repo_copy_is_blocked_and_governed_copy_passes(tmp_path: Path) -> None:
    live_scripts = tmp_path / "live/releases/budget/digest/scripts"
    live_scripts.mkdir(parents=True)
    for source in SCRIPTS.iterdir():
        if source.is_file():
            shutil.copy2(source, live_scripts / source.name)
    (tmp_path / "live/budget").symlink_to(Path("releases/budget/digest"))

    blocked = _run(tmp_path, "snapshot")
    assert blocked.returncode == 3
    assert skill_mount.STALE_COPY_MARKER in blocked.stderr

    governed_cli = live_scripts / "budget_cli.py"
    env = {**os.environ, "AUTOPHAGY_REPO_ROOT": str(ROOT),
           "AUTOPHAGY_SKILL_LIVE_ROOT": str(tmp_path / "live")}
    passed = subprocess.run([sys.executable, str(governed_cli), "snapshot"], cwd=ROOT,
                            env=env, capture_output=True, text=True, check=False)
    assert skill_mount.STALE_COPY_MARKER not in passed.stderr


def test_read_only_repo_copy_is_not_blocked(tmp_path: Path) -> None:
    result = _run(tmp_path, "sheets")
    assert result.returncode != 3 or skill_mount.STALE_COPY_MARKER not in result.stderr


def _load_governed():
    """sys.path 를 건드리지 않고 스킬 사본을 그대로 읽는다 — 이웃 테스트가 남긴 경로에 기대면
    실행 순서에 따라 ModuleNotFoundError 가 난다(2026-09-03 전체 스위트 실측)."""
    path = ROOT / "skills/budget/scripts/budget_governed.py"
    spec = importlib.util.spec_from_file_location("budget_governed_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_constants_match_shared_definition() -> None:
    budget_governed = _load_governed()

    assert budget_governed.GOVERNED_LIVE_ROOT == skill_mount.LIVE_ROOT
    assert budget_governed.LIVE_ROOT_ENV == skill_mount.LIVE_ROOT_ENV
    assert budget_governed.STALE_COPY_MARKER == skill_mount.STALE_COPY_MARKER
