"""Wiki CLI governed-copy guard regression tests."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation import skill_mount

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "skills/wiki/scripts/wiki_cli.py"
SCRIPTS = ROOT / "skills/wiki/scripts"


def _run(root: Path, *args: str, script: Path = SCRIPT) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AUTOPHAGY_SKILL_LIVE_ROOT"] = str(root / "live")
    env["WIKI_ROOT"] = str(root / "wiki")
    return subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True, env=env
    )


def test_constants_match_canonical() -> None:
    import skills.wiki.scripts.wiki_governed as governed

    assert governed.GOVERNED_LIVE_ROOT == skill_mount.LIVE_ROOT
    assert governed.LIVE_ROOT_ENV == skill_mount.LIVE_ROOT_ENV
    assert governed.STALE_COPY_MARKER == skill_mount.STALE_COPY_MARKER


def test_repo_copy_mutation_blocked_but_read_only_allowed(tmp_path: Path) -> None:
    live_scripts = tmp_path / "live/releases/wiki/digest/scripts"
    live_scripts.mkdir(parents=True)
    for source in SCRIPTS.iterdir():
        if source.is_file():
            shutil.copy2(source, live_scripts / source.name)
    (tmp_path / "live/wiki").symlink_to(tmp_path / "live/releases/wiki/digest", target_is_directory=True)

    blocked = _run(tmp_path, "draft", "--title", "x")
    assert blocked.returncode == 3
    assert "STALE-SKILL-COPY-BLOCK" in blocked.stderr

    readonly = _run(tmp_path, "query", "x")
    assert "STALE-SKILL-COPY-BLOCK" not in readonly.stderr


def test_governed_copy_is_not_blocked(tmp_path: Path) -> None:
    governed_scripts = tmp_path / "live/wiki/scripts"
    governed_scripts.mkdir(parents=True)
    for source in SCRIPTS.iterdir():
        if source.is_file():
            shutil.copy2(source, governed_scripts / source.name)
    result = _run(tmp_path, "draft", "--title", "x", script=governed_scripts / "wiki_cli.py")
    assert "STALE-SKILL-COPY-BLOCK" not in result.stderr
