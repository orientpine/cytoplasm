"""prompt CLI의 stale copy guard를 고정한다."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation import skill_mount

ROOT = Path(__file__).parents[2]
CLI = ROOT / "skills/prompt/scripts/prompt_cli.py"
SCRIPTS = ROOT / "skills/prompt/scripts"


def _args(body: Path) -> tuple[str, ...]:
    return ("add", "--id", "guard-test", "--category", "task", "--purpose", "test",
            "--model", "any", "--tags", "test", "--body-file", str(body))


def _run(cli: Path, live_root: Path, body: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "AUTOPHAGY_REPO_ROOT": str(ROOT),
           "AUTOPHAGY_SKILL_LIVE_ROOT": str(live_root), "HOME": str(live_root / "home")}
    return subprocess.run([sys.executable, str(cli), *_args(body)], cwd=cli.parent, env=env,
                          capture_output=True, text=True, check=False)


def test_stale_copy_mutation_is_refused(tmp_path: Path) -> None:
    stale = tmp_path / "stale/skills/prompt/scripts"
    stale.mkdir(parents=True)
    for source in SCRIPTS.iterdir():
        if source.is_file():
            shutil.copy2(source, stale / source.name)
    live = tmp_path / "live"
    live_scripts = live / "other-layout/prompt/scripts"
    live_scripts.mkdir(parents=True)
    for source in SCRIPTS.iterdir():
        if source.is_file():
            shutil.copy2(source, live_scripts / source.name)
    (live / "prompt").symlink_to(Path("other-layout/prompt"))
    body = tmp_path / "body.md"
    body.write_text("body", encoding="utf-8")

    result = _run(stale / "prompt_cli.py", live, body)

    assert result.returncode == 3
    assert skill_mount.STALE_COPY_MARKER in result.stderr


def test_missing_live_skill_does_not_trigger_guard(tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("body", encoding="utf-8")
    result = _run(CLI, tmp_path / "live", body)
    assert skill_mount.STALE_COPY_MARKER not in result.stderr


def test_governed_module_constants_match_shared_definition() -> None:
    path = ROOT / "skills/prompt/scripts/prompt_governed.py"
    spec = importlib.util.spec_from_file_location("prompt_governed_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.GOVERNED_LIVE_ROOT == skill_mount.LIVE_ROOT
    assert module.LIVE_ROOT_ENV == skill_mount.LIVE_ROOT_ENV
    assert module.SKILL_NAME == "prompt"
    assert module.STALE_COPY_MARKER == skill_mount.STALE_COPY_MARKER
