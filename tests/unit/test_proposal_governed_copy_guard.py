"""proposal CLI의 배포 사본 판정을 고정한다."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation import skill_mount

ROOT = Path(__file__).parents[2]
CLI = ROOT / "skills/proposal/scripts/proposal_cli.py"
SCRIPTS = ROOT / "skills/proposal/scripts"


def _run(copied_cli: Path, live_root: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "AUTOPHAGY_REPO_ROOT": str(ROOT),
        "AUTOPHAGY_SKILL_LIVE_ROOT": str(live_root),
    }
    return subprocess.run(
        [sys.executable, str(copied_cli), "create", "--slug", "demo", "--title", "Demo", "--section", "body:Body"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _copy_scripts(destination: Path) -> Path:
    destination.mkdir(parents=True)
    for source in SCRIPTS.iterdir():
        if source.is_file():
            shutil.copy2(source, destination / source.name)
    return destination / "proposal_cli.py"


def test_stale_copy_is_blocked_and_missing_live_skill_is_allowed(tmp_path: Path) -> None:
    copied_cli = _copy_scripts(tmp_path / "stale" / "proposal" / "scripts")
    live_root = tmp_path / "live"
    (live_root / "proposal" / "scripts").mkdir(parents=True)
    blocked = _run(copied_cli, live_root)
    assert blocked.returncode == 3
    assert skill_mount.STALE_COPY_MARKER in blocked.stderr

    missing_live = _run(copied_cli, tmp_path / "other-live")
    assert skill_mount.STALE_COPY_MARKER not in missing_live.stderr
    assert missing_live.returncode != 3
