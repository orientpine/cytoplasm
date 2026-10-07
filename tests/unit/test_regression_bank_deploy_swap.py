"""RCB todo 19: the regression_bank runtime is replaced through `deploy_tree_swap`.

The old deployer ran `rm -rf; mkdir; tar -x` in place, so a reader starting in that window
saw no tree or half a tree. The runtime is a row of the standing runtime-package table, so it
must stay a real directory (no `--link`) and ship exactly the declared file list.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_PKG: Final = _REPO / "automation" / "regression_bank"
_DEPLOYER: Final = _PKG / "deploy.sh"
_RUNTIME_DEST: Final = ".hermes/regression_bank_runtime"
_FILES: Final = ("bank_state.py", "weekly_bank.py")


def _rig(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    home, bin_dir, ops = tmp_path / "home", tmp_path / "bin", tmp_path / "ops"
    (home / ".hermes").mkdir(parents=True)
    bin_dir.mkdir()
    ops.mkdir()
    (home / ".hermes/config.yaml").write_text("timezone: Asia/Seoul\n", encoding="utf-8")
    (bin_dir / "ssh").write_text('#!/usr/bin/env bash\nshift\nexec bash -c "$*"\n', encoding="utf-8")
    (bin_dir / "sudo").write_text(
        '#!/usr/bin/env bash\nwhile [[ "$1" == -* ]]; do case "$1" in -u) shift 2;; *) shift;; esac; done\n'
        'if [[ "${FAULT:-}" == corrupt-prepare && "$*" == *_deploy_tree_remote_prepare* ]]; then\n'
        '  HOME="$NODE_HOME" "$@" >/dev/null; echo corrupted; exit 0\n'
        'fi\n'
        'if [[ "$*" == */srv/autophagy-agents/logs* ]]; then : > "$LATER_STEP_MARK"; exit 0; fi\n'
        'HOME="$NODE_HOME" exec "$@"\n',
        encoding="utf-8",
    )
    (bin_dir / "hermes").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for name in ("ssh", "sudo", "hermes"):
        (bin_dir / name).chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}", "NODE_HOME": str(home), "HOME": str(ops),
        "DEPLOY_SSH_HOST": "fake-host", "DEPLOY_ALLOW_UNPUSHED": "1", "LANG": "C.UTF-8",
        "LATER_STEP_MARK": str(tmp_path / "later-step-ran"),
    }
    return home, env


def _deploy(env: dict[str, str], fault: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", str(_DEPLOYER)), env={**env, "FAULT": fault}, cwd=_REPO,
        capture_output=True, text=True, check=False, timeout=120,
    )


def test_regression_bank_deploy_uses_the_swap_helper(tmp_path: Path) -> None:
    home, env = _rig(tmp_path)
    later_step = Path(env["LATER_STEP_MARK"])
    runtime = home / _RUNTIME_DEST
    runtime.mkdir()
    (runtime / "bank_state.py").write_text("old\n", encoding="utf-8")
    before = (runtime / "bank_state.py").read_bytes()

    failed = _deploy(env, "corrupt-prepare")
    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert sorted(p.name for p in runtime.iterdir()) == ["bank_state.py"]
    assert (runtime / "bank_state.py").read_bytes() == before
    assert not later_step.exists()
    assert not list((home / ".hermes").glob("regression_bank_runtime.staging.*"))

    done = _deploy(env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert later_step.exists()
    assert runtime.is_dir() and not runtime.is_symlink()
    assert sorted(p.name for p in runtime.iterdir()) == sorted(_FILES)
    for name in _FILES:
        assert (runtime / name).read_bytes() == (_PKG / name).read_bytes()
    assert list((home / ".hermes").glob("regression_bank_runtime.old.*"))
