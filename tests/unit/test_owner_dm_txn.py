"""Fault-injection tests for the owner-DM transactional deploy core.

owner-dm-txn.sh is path-based (no SSH) so we can drive it with stub appliers and
assert that it either commits ALL mutated files or restores every one to its
pre-run bytes.
"""

from __future__ import annotations

import subprocess

import pytest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_TXN = _ROOT / "automation" / "hermes_compat" / "owner-dm-txn.sh"
_RESTORE = _ROOT / "automation" / "hermes_compat" / "owner-dm-restore.sh"

_GOOD_APPLIER = (
    "import sys\n"
    "cmd, target = sys.argv[1], sys.argv[2]\n"
    "if cmd == 'apply':\n"
    "    open(target, 'a').write('# PATCHED\\n')\n"
    "elif cmd == 'verify':\n"
    "    sys.exit(0 if '# PATCHED' in open(target).read() else 3)\n"
)
_FAILING_APPLIER = "import sys\nsys.exit(1)\n"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")
    return path


def _layout(
    tmp_path: Path, receipts_applier_src: str, *, existing_runtime: bool = True
) -> dict[str, Path]:
    run_py = _write(tmp_path / "run.py", "RUN = 1\n")
    adapter_py = _write(tmp_path / "adapter.py", "ADAPTER = 1\n")
    busy = _write(tmp_path / "busy_applier.py", _GOOD_APPLIER)
    receipts = _write(tmp_path / "receipts_applier.py", receipts_applier_src)
    staging = tmp_path / "staging"
    _ = _write(staging / "hermes_compat_boot.py", "NEW BOOT\n")
    _ = _write(staging / "automation" / "hermes_compat" / "mod.py", "NEW MOD\n")
    active = tmp_path / "active"
    active.mkdir(parents=True, exist_ok=True)
    if existing_runtime:
        _ = _write(active / "hermes_compat_boot.py", "OLD BOOT\n")
        _ = _write(active / "automation" / "hermes_compat" / "mod.py", "OLD MOD\n")
    snap = tmp_path / "snap"
    return {
        "run_py": run_py,
        "adapter_py": adapter_py,
        "busy": busy,
        "receipts": receipts,
        "staging": staging,
        "active": active,
        "snap": snap,
    }


def _run(paths: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(_TXN),
            str(paths["run_py"]),
            str(paths["adapter_py"]),
            str(paths["busy"]),
            str(paths["receipts"]),
            str(paths["staging"]),
            str(paths["active"]),
            str(paths["snap"]),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_txn_commits_and_activates_runtime_on_success(tmp_path: Path) -> None:
    # Given
    paths = _layout(tmp_path, _GOOD_APPLIER)

    # When
    result = _run(paths)

    # Then
    assert result.returncode == 0, result.stderr
    assert "COMMIT-OK" in result.stdout
    assert "# PATCHED" in paths["run_py"].read_text(encoding="utf-8")
    assert "# PATCHED" in paths["adapter_py"].read_text(encoding="utf-8")
    # Runtime activated to the staged (new) modules.
    assert (paths["active"] / "hermes_compat_boot.py").read_text(encoding="utf-8") == "NEW BOOT\n"
    assert (
        paths["active"] / "automation" / "hermes_compat" / "mod.py"
    ).read_text(encoding="utf-8") == "NEW MOD\n"


def test_txn_rolls_back_every_file_when_receipts_apply_fails(tmp_path: Path) -> None:
    # Given: the receipts applier fails AFTER the busy-fifo applier already patched run.py
    # and the runtime was already activated.
    paths = _layout(tmp_path, _FAILING_APPLIER)

    # When
    result = _run(paths)

    # Then: non-zero, rollback reported, and EVERY mutated file restored to pre-run bytes.
    assert result.returncode != 0
    assert "ROLLBACK-OK" in result.stderr
    assert paths["run_py"].read_text(encoding="utf-8") == "RUN = 1\n"
    assert paths["adapter_py"].read_text(encoding="utf-8") == "ADAPTER = 1\n"
    assert (paths["active"] / "hermes_compat_boot.py").read_text(encoding="utf-8") == "OLD BOOT\n"
    assert (
        paths["active"] / "automation" / "hermes_compat" / "mod.py"
    ).read_text(encoding="utf-8") == "OLD MOD\n"


def test_txn_fails_closed_when_snapshot_dir_cannot_be_created(tmp_path: Path) -> None:
    # Given: a snapshot path blocked by an existing FILE (mkdir -p must fail).
    paths = _layout(tmp_path, _GOOD_APPLIER)
    blocker = tmp_path / "blocked"
    _ = blocker.write_text("not a dir\n", encoding="utf-8")
    paths["snap"] = blocker

    # When
    result = _run(paths)

    # Then: refuse before mutating the live source at all.
    assert result.returncode != 0
    assert paths["run_py"].read_text(encoding="utf-8") == "RUN = 1\n"
    assert paths["adapter_py"].read_text(encoding="utf-8") == "ADAPTER = 1\n"



def test_txn_rollback_removes_runtime_activated_on_a_first_deploy(tmp_path: Path) -> None:
    # First deploy: no runtime exists in the live import root yet. A failure must
    # REMOVE the runtime the transaction activated, not leave it half-installed.
    paths = _layout(tmp_path, _FAILING_APPLIER, existing_runtime=False)

    result = _run(paths)

    assert result.returncode != 0
    assert "ROLLBACK-OK" in result.stderr
    assert paths["run_py"].read_text(encoding="utf-8") == "RUN = 1\n"
    assert paths["adapter_py"].read_text(encoding="utf-8") == "ADAPTER = 1\n"
    # Runtime that was absent pre-run is gone again (not left activated).
    assert not (paths["active"] / "hermes_compat_boot.py").exists()
    assert not (paths["active"] / "automation" / "hermes_compat").exists()
    # The parent dir the transaction created is removed too (nothing left behind).
    assert not (paths["active"] / "automation").exists()


def _full_manifest(**overrides: str) -> str:
    keys = {
        "bootstrap": "absent",
        "bootstrap_cache": "absent",
        "package": "absent",
        "automation_parent": "absent",
        "run_backup": "absent",
        "adapter_backup": "absent",
    }
    keys.update(overrides)
    return "".join(f"{name}={value}\n" for name, value in keys.items())


def _run_restore_with_state(
    tmp_path: Path, state_text: str
) -> subprocess.CompletedProcess[str]:
    run_py = _write(tmp_path / "run.py", "RUN = 1\n")
    adapter_py = _write(tmp_path / "adapter.py", "ADAPTER = 1\n")
    active = tmp_path / "active"
    active.mkdir()
    snap = tmp_path / "snap"
    _ = _write(snap / "run.py", "RUN = 0\n")
    _ = _write(snap / "adapter.py", "ADAPTER = 0\n")
    (snap / "runtime").mkdir()
    _ = _write(snap / "state", state_text)
    return subprocess.run(
        ["bash", str(_RESTORE), str(run_py), str(adapter_py), str(active), str(snap)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "state_text",
    [
        # Truncated: the 'package' key is missing entirely.
        _full_manifest().replace("package=absent\n", ""),
        # Malformed value: 'package=absent=garbage' must NOT be read as 'absent'.
        _full_manifest(package="absent=garbage"),
        # Duplicated key: two 'bootstrap' lines must be rejected.
        _full_manifest() + "bootstrap=present\n",
    ],
)
def test_restore_fails_closed_on_bad_manifest(tmp_path: Path, state_text: str) -> None:
    # A restore that cannot trust the manifest must fail closed (never ROLLBACK-OK)
    # and must refuse before touching the live source.
    result = _run_restore_with_state(tmp_path, state_text)

    assert result.returncode == 1
    assert "ROLLBACK-FAILED" in result.stderr
    assert (tmp_path / "run.py").read_text(encoding="utf-8") == "RUN = 1\n"


def _pyc(active: Path, content: str) -> Path:
    return _write(active / "__pycache__" / "hermes_compat_boot.cpython-312.pyc", content)


def _cache_snapshot(tmp_path: Path, *, cache_present: bool) -> dict[str, Path]:
    run_py = _write(tmp_path / "run.py", "RUN = 1\n")
    adapter_py = _write(tmp_path / "adapter.py", "ADAPTER = 1\n")
    active = tmp_path / "active"
    _ = _write(active / "hermes_compat_boot.py", "OLD BOOT\n")
    _ = _pyc(active, "NEWPYC")  # a failed gateway start wrote/updated this
    snap = tmp_path / "snap"
    _ = _write(snap / "run.py", "RUN = 0\n")
    _ = _write(snap / "adapter.py", "ADAPTER = 0\n")
    _ = _write(snap / "runtime" / "hermes_compat_boot.py", "OLD BOOT\n")
    if cache_present:
        _ = _write(snap / "runtime" / "__pycache__" / "hermes_compat_boot.cpython-312.pyc", "OLDPYC")
    _ = _write(
        snap / "state",
        _full_manifest(bootstrap="present", bootstrap_cache="present" if cache_present else "absent"),
    )
    return {"run_py": run_py, "adapter_py": adapter_py, "active": active, "snap": snap}


def _run_restore(paths: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(_RESTORE),
            str(paths["run_py"]),
            str(paths["adapter_py"]),
            str(paths["active"]),
            str(paths["snap"]),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_restore_removes_bootstrap_cache_absent_pre_run(tmp_path: Path) -> None:
    # bootstrap existed pre-run but its __pycache__ did NOT; a failed gateway start
    # wrote a fresh pyc -> rollback must remove the whole cache dir (exact pre-run).
    paths = _cache_snapshot(tmp_path, cache_present=False)

    result = _run_restore(paths)

    assert result.returncode == 0, result.stderr
    assert "ROLLBACK-OK" in result.stdout
    assert not (paths["active"] / "__pycache__").exists()


def test_restore_restores_bootstrap_cache_present_pre_run(tmp_path: Path) -> None:
    # bootstrap AND its cache existed pre-run; rollback restores the ORIGINAL pyc bytes,
    # not the ones a failed gateway start wrote.
    paths = _cache_snapshot(tmp_path, cache_present=True)

    result = _run_restore(paths)

    assert result.returncode == 0, result.stderr
    assert "ROLLBACK-OK" in result.stdout
    restored = paths["active"] / "__pycache__" / "hermes_compat_boot.cpython-312.pyc"
    assert restored.read_text(encoding="utf-8") == "OLDPYC"