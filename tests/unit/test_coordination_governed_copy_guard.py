"""coordination 변경 명령의 배포 사본 경계를 고정한다."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation import skill_mount

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "skills/coordination/scripts/coordinate_cli.py"
MARKER = "STALE-SKILL-COPY-BLOCK"


def _run(script: Path, live: Path, command: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AUTOPHAGY_SKILL_LIVE_ROOT"] = str(live)
    return subprocess.run(
        [sys.executable, str(script), command, "--peer", "p", "--summary", "s", "--when", "오후"],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False,
    )


def _load_governed():
    """sys.path 를 건드리지 않고 스킬 사본을 그대로 읽는다 — 이웃 테스트가 남긴 경로에 기대면
    실행 순서에 따라 ModuleNotFoundError 가 난다(2026-09-03 전체 스위트 실측)."""
    path = ROOT / "skills/coordination/scripts/coordination_governed.py"
    spec = importlib.util.spec_from_file_location("coordination_governed_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_constants_match_canonical() -> None:
    coordination_governed = _load_governed()

    assert coordination_governed.GOVERNED_LIVE_ROOT == skill_mount.LIVE_ROOT
    assert coordination_governed.LIVE_ROOT_ENV == skill_mount.LIVE_ROOT_ENV
    assert coordination_governed.STALE_COPY_MARKER == skill_mount.STALE_COPY_MARKER


def test_repo_mutation_is_blocked_but_read_only_is_not(tmp_path: Path) -> None:
    live = tmp_path / "live"
    release = live / "releases/coordination/digest"
    scripts = release / "scripts"
    scripts.mkdir(parents=True)
    for source in (ROOT / "skills/coordination/scripts").glob("*.py"):
        shutil.copy2(source, scripts / source.name)
    (live / "coordination").symlink_to(release)

    blocked = _run(SCRIPT, live, "request")
    assert blocked.returncode == 3
    assert MARKER in blocked.stderr

    governed = _run(scripts / "coordinate_cli.py", live, "request")
    assert MARKER not in governed.stderr

    readonly = subprocess.run(
        [sys.executable, str(SCRIPT), "status"], cwd=ROOT, env={**os.environ, "AUTOPHAGY_SKILL_LIVE_ROOT": str(live)},
        text=True, capture_output=True, check=False,
    )
    assert MARKER not in readonly.stderr
