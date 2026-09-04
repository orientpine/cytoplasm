from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import automation.skill_mount as skill_mount

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "skills/calendar/scripts/calendar_cli.py"


def _run(script: Path, *args: str, live: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(AUTOPHAGY_SKILL_LIVE_ROOT=str(live), AUTOPHAGY_REPO_ROOT=str(ROOT))
    return subprocess.run(
        ["python3", str(script), *args], cwd=ROOT, env=env,
        capture_output=True, text=True, check=False,
    )


def test_constants_match_canonical() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("calendar_governed", ROOT / "skills/calendar/scripts/calendar_governed.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.GOVERNED_LIVE_ROOT == skill_mount.LIVE_ROOT
    assert module.LIVE_ROOT_ENV == skill_mount.LIVE_ROOT_ENV
    assert module.STALE_COPY_MARKER == skill_mount.STALE_COPY_MARKER


def test_mutations_are_blocked_but_reads_are_not(tmp_path: Path) -> None:
    live = tmp_path / "live"
    release = live / "releases/calendar/digest"
    release.mkdir(parents=True)
    shutil.copytree(ROOT / "skills/calendar/scripts", release / "scripts")
    (live / "calendar").symlink_to(Path("releases/calendar/digest"))

    blocked = _run(CLI, "draft-delete", "--event-id", "x", live=live)
    assert blocked.returncode == 3
    assert skill_mount.STALE_COPY_MARKER in blocked.stderr

    governed = _run(release / "scripts/calendar_cli.py", "draft-delete", "--event-id", "x", live=live)
    assert skill_mount.STALE_COPY_MARKER not in governed.stderr

    read = _run(CLI, "list-drafts", live=live)
    assert skill_mount.STALE_COPY_MARKER not in read.stderr
