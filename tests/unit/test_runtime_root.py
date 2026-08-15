"""Runtime-root resolver: the one place every runtime consumer asks "where does
my code live?" — the immutable release `current` symlink if it exists, else the
resident /srv/autophagy-agents mirror (backwards-compatible fallback).

WHY (2026-07-31): DG-4 migrates the runtime off the mutable resident checkout onto
an immutable release/current path. A shared resolver makes the migration
fallback-safe: with `current` absent, resolve_runtime_root returns the old mirror,
so merging DG-2..DG-4 is a behavioural NO-OP until the node flip creates the
symlink. The Python and bash resolvers must agree byte-for-byte.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_PY = _REPO / "automation" / "runtime_root.py"
_SH = _REPO / "automation" / "runtime_root.sh"


def _load_py():
    spec = importlib.util.spec_from_file_location("runtime_root", _PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _resolve_sh(env: dict[str, str], current: Path, mirror: Path) -> str:
    script = (
        f'source "{_SH}"\n'
        f'RUNTIME_RELEASE_CURRENT="{current}" RUNTIME_MIRROR_CHECKOUT="{mirror}" '
        f'autophagy_runtime_root\n'
    )
    result = subprocess.run(
        ("bash", "-c", script), capture_output=True, text=True, check=False, env=env
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _resolve_py(module, env: dict[str, str], current: Path, mirror: Path) -> str:
    return str(module.resolve_runtime_root(env, current=current, mirror=mirror))


def test_falls_back_to_mirror_when_current_absent(tmp_path: Path) -> None:
    # THE no-op proof: with no `current`, every consumer keeps using the mirror,
    # so merging DG-2..DG-4 changes nothing live.
    module = _load_py()
    current = tmp_path / "current"   # absent
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    assert _resolve_py(module, {}, current, mirror) == str(mirror)
    assert _resolve_sh({}, current, mirror) == str(mirror)


def test_prefers_current_when_present(tmp_path: Path) -> None:
    module = _load_py()
    release = tmp_path / "release"
    release.mkdir()
    current = tmp_path / "current"
    current.symlink_to(release, target_is_directory=True)
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    assert _resolve_py(module, {}, current, mirror) == str(current)
    assert _resolve_sh({}, current, mirror) == str(current)


def test_env_override_wins(tmp_path: Path) -> None:
    module = _load_py()
    override = tmp_path / "override"
    override.mkdir()
    current = tmp_path / "current"
    current.symlink_to(tmp_path, target_is_directory=True)  # present but overridden
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    env = {"AUTOPHAGY_RUNTIME_ROOT": str(override)}
    assert _resolve_py(module, env, current, mirror) == str(override)
    assert _resolve_sh(env, current, mirror) == str(override)


def test_shell_and_python_resolvers_agree(tmp_path: Path) -> None:
    module = _load_py()
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    # three fixtures: absent current, present current, env override
    fixtures: list[tuple[dict[str, str], Path]] = []
    absent = tmp_path / "c_absent"
    fixtures.append(({}, absent))
    present = tmp_path / "c_present"
    present.symlink_to(mirror, target_is_directory=True)
    fixtures.append(({}, present))
    override = tmp_path / "ovr"
    override.mkdir()
    fixtures.append(({"AUTOPHAGY_RUNTIME_ROOT": str(override)}, present))
    for env, current in fixtures:
        assert _resolve_py(module, env, current, mirror) == _resolve_sh(env, current, mirror)
