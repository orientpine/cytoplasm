"""memory_curator 워처는 릴리스 트리의 패키지를 실행한다(RCB todo 14, GAP-2).

래퍼는 예전에 계정 홈의 사본(`.hermes/memory_curator_runtime`)을 `sys.path` 맨 앞에 넣고
평면 이름 `memory_curator.…` 로 import 했다. 그 사본은 릴리스 수렴이 보지 않아 조용히 낡았다.
여기서는 래퍼를 별도 프로세스에서 임시 HOME 으로 돌려 어느 파일이 실제로 import 되는지만 본다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_WRAPPER: Final = _REPO / "automation" / "memory_curator" / "cron" / "memory_curator_watch.py"
_DECOY: Final = "raise SystemExit(99)\n"
_LOAD: Final = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('memory_curator_watch', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "effects, watch = module.load_modules()\n"
    "print(watch.__file__)\n"
)


def _run(home: Path, root: str, *argv: str) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "AUTOPHAGY_REPO_ROOT": root,
    }
    return subprocess.run(
        [sys.executable, *argv],
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _plant_stale_home_copy(home: Path) -> None:
    package = home / ".hermes" / "memory_curator_runtime" / "memory_curator"
    package.mkdir(parents=True)
    for name in ("__init__.py", "watch.py", "effects.py"):
        _ = (package / name).write_text(_DECOY, encoding="utf-8")


def test_modules_are_loaded_from_the_runtime_root(tmp_path: Path) -> None:
    result = _run(tmp_path, str(_REPO), "-c", _LOAD, str(_WRAPPER))

    assert result.returncode == 0, result.stderr
    loaded = Path(result.stdout.strip().splitlines()[-1]).resolve()
    assert loaded.is_relative_to(_REPO.resolve() / "automation" / "memory_curator")


def test_a_stale_home_copy_is_never_imported(tmp_path: Path) -> None:
    _plant_stale_home_copy(tmp_path)

    tick = _run(tmp_path, str(_REPO), str(_WRAPPER))
    loaded = _run(tmp_path, str(_REPO), "-c", _LOAD, str(_WRAPPER))

    assert tick.returncode != 99, tick.stderr
    assert loaded.returncode == 0, loaded.stderr
    stale = tmp_path / ".hermes" / "memory_curator_runtime"
    assert not Path(loaded.stdout.strip().splitlines()[-1]).resolve().is_relative_to(stale)


def test_a_missing_runtime_root_fails_loudly(tmp_path: Path) -> None:
    _plant_stale_home_copy(tmp_path)

    result = _run(tmp_path, "/nonexistent", "-c", _LOAD, str(_WRAPPER))

    assert result.returncode not in (0, 99)
    assert "ModuleNotFoundError" in result.stderr
