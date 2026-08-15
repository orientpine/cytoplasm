"""Single-instance flock guard for the rag-ingest cron watcher (subprocess-level).

The first obsidian bootstrap (~2240 files) can outlive the 10-minute cron
interval, so an overlapping tick must be a silent no-op instead of a second
competing pipeline run. The stub runtime package below stands in for the
deployed ``~/.hermes/rag_ingest_runtime/rag_ingest`` — no real config,
network, or git is touched.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_WATCH_SCRIPT = _REPO / "automation" / "rag_ingest" / "cron" / "rag_ingest_watch.py"

_STUB_CLI = '''\
"""Stub rag_ingest.cli: records invocations, blocks until released."""
import os
import time
from pathlib import Path


def main(argv=None):
    with Path(os.environ["STUB_CALLS_FILE"]).open("a", encoding="utf-8") as handle:
        _ = handle.write("call\\n")
    _ = Path(os.environ["STUB_READY_FILE"]).write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 30.0
    while not Path(os.environ["STUB_RELEASE_FILE"]).exists():
        if time.monotonic() > deadline:
            return 2
        time.sleep(0.02)
    return 0
'''


def make_watch_environment(tmp_path: Path) -> dict[str, str]:
    """Fake HOME with a stub runtime package; env wires the stub's rendezvous files."""
    home = tmp_path / "home"
    package_dir = home / ".hermes" / "rag_ingest_runtime" / "rag_ingest"
    package_dir.mkdir(parents=True)
    _ = (package_dir / "__init__.py").write_text("", encoding="utf-8")
    _ = (package_dir / "cli.py").write_text(_STUB_CLI, encoding="utf-8")
    return {
        **os.environ,
        "HOME": str(home),
        "STUB_CALLS_FILE": str(tmp_path / "calls.log"),
        "STUB_READY_FILE": str(tmp_path / "ready"),
        "STUB_RELEASE_FILE": str(tmp_path / "release"),
    }


def wait_for(path: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"timed out waiting for {path}"
        time.sleep(0.02)


def run_watch_tick(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_WATCH_SCRIPT)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_overlapping_tick_skips_silently_while_first_run_holds_lock(tmp_path: Path) -> None:
    # Given — a first tick still mid-run (stub blocks until released)
    environment = make_watch_environment(tmp_path)
    first = subprocess.Popen(
        [sys.executable, str(_WATCH_SCRIPT)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for(Path(environment["STUB_READY_FILE"]))

        # When — a second cron tick fires while the first is still running
        second = run_watch_tick(environment)

        # Then — silent no-op: exit 0, no output, pipeline NOT invoked again
        assert second.returncode == 0
        assert second.stdout == ""
        calls = Path(environment["STUB_CALLS_FILE"]).read_text(encoding="utf-8")
        assert calls.count("call") == 1

        _ = Path(environment["STUB_RELEASE_FILE"]).write_text("go", encoding="utf-8")
        assert first.wait(timeout=30) == 0
    finally:
        if first.poll() is None:
            first.kill()
            _ = first.wait(timeout=10)


def test_lock_is_released_after_run_so_next_tick_proceeds(tmp_path: Path) -> None:
    # Given — a completed first tick (release pre-created: stub returns at once)
    environment = make_watch_environment(tmp_path)
    _ = Path(environment["STUB_RELEASE_FILE"]).write_text("go", encoding="utf-8")
    first = run_watch_tick(environment)
    assert first.returncode == 0

    # When — the next tick fires after the first finished
    second = run_watch_tick(environment)

    # Then — the lock did not stick: the pipeline ran again
    assert second.returncode == 0
    calls = Path(environment["STUB_CALLS_FILE"]).read_text(encoding="utf-8")
    assert calls.count("call") == 2
