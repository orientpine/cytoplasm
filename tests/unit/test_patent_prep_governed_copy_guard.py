"""patent-prep CLI의 배포 사본 판정을 고정한다."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation import skill_mount

ROOT = Path(__file__).parents[2]
CLI = ROOT / "skills/patent-prep/scripts/patent_cli.py"
SCRIPTS = ROOT / "skills/patent-prep/scripts"


def _run(cli: Path, live_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "AUTOPHAGY_REPO_ROOT": str(ROOT),
        "AUTOPHAGY_SKILL_LIVE_ROOT": str(live_root),
        "PATENT_DRAFT_ROOT": str(live_root.parent / "drafts"),
        "PATENT_STATUS_ROOT": str(live_root.parent / "status"),
    }
    return subprocess.run(
        [sys.executable, str(cli), *args], cwd=ROOT, env=env,
        capture_output=True, text=True, check=False,
    )


def test_stale_copy_mutation_is_blocked(tmp_path: Path) -> None:
    stale = tmp_path / "stale/scripts"
    stale.parent.mkdir()
    shutil.copytree(SCRIPTS, stale)
    live = tmp_path / "live"
    (live / "patent-prep/scripts").mkdir(parents=True)

    result = _run(stale / "patent_cli.py", live, "create", "--slug", "test")

    assert result.returncode == 3
    assert skill_mount.STALE_COPY_MARKER in result.stderr


def test_missing_live_skill_does_not_fire_guard(tmp_path: Path) -> None:
    stale = tmp_path / "stale/scripts"
    stale.parent.mkdir()
    shutil.copytree(SCRIPTS, stale)

    result = _run(stale / "patent_cli.py", tmp_path / "live", "create", "--slug", "test")

    assert skill_mount.STALE_COPY_MARKER not in result.stderr


def _load_governed():
    path = ROOT / "skills/patent-prep/scripts/patent_prep_governed.py"
    spec = importlib.util.spec_from_file_location("patent_prep_governed_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_constants_match_shared_definition() -> None:
    governed = _load_governed()
    assert governed.GOVERNED_LIVE_ROOT == skill_mount.LIVE_ROOT
    assert governed.LIVE_ROOT_ENV == skill_mount.LIVE_ROOT_ENV
    assert governed.SKILL_NAME == "patent-prep"
    assert governed.STALE_COPY_MARKER == skill_mount.STALE_COPY_MARKER
