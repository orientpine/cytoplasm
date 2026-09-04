"""meeting CLI governed-copy refusal contract."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPTS = ROOT / "skills/meeting/scripts"
CLI = SCRIPTS / "meeting_cli.py"


def _run(cli: Path, live_root: Path) -> subprocess.CompletedProcess[str]:
    source = cli.parent / "input.md"
    source.write_text("회의 내용", encoding="utf-8")
    env = {
        **os.environ,
        "AUTOPHAGY_REPO_ROOT": str(ROOT),
        "AUTOPHAGY_SKILL_LIVE_ROOT": str(live_root),
    }
    return subprocess.run(
        [sys.executable, str(cli), "ingest", "--file", str(source)],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )


def test_stale_copy_mutation_is_refused(tmp_path: Path) -> None:
    stale = tmp_path / "stale"
    stale.mkdir()
    for source in SCRIPTS.iterdir():
        if source.is_file():
            shutil.copy2(source, stale / source.name)
    live = tmp_path / "live"
    (live / "meeting/scripts").mkdir(parents=True)
    result = _run(stale / "meeting_cli.py", live)
    assert result.returncode == 3
    assert "STALE-SKILL-COPY-BLOCK" in result.stderr


def test_missing_live_skill_does_not_refuse(tmp_path: Path) -> None:
    stale = tmp_path / "stale"
    stale.mkdir()
    for source in SCRIPTS.iterdir():
        if source.is_file():
            shutil.copy2(source, stale / source.name)
    result = _run(stale / "meeting_cli.py", tmp_path / "live")
    assert result.returncode != 3
    assert "STALE-SKILL-COPY-BLOCK" not in result.stderr
