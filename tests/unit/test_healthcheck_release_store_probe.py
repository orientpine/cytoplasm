from __future__ import annotations

import subprocess
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_HEALTHCHECK = _REPO / "automation" / "healthcheck.sh"
_PROBE = _REPO / "automation" / "release_store_probe.sh"
_SHA = "a" * 40


def _run_probe(store: Path, *, max_generations: int = 6, max_bytes: int = 1 << 30) -> subprocess.CompletedProcess[str]:
    script = (
        f'source "{_PROBE}"; '
        f'RELEASE_STORE_MAX_GENERATIONS={max_generations} '
        f'RELEASE_STORE_MAX_BYTES={max_bytes} '
        f'probe_release_store_usage node ops "{store}"'
    )
    return subprocess.run(("bash", "-c", script), capture_output=True, text=True, check=False)


def test_release_store_probe_reports_generation_count_and_disk_usage(tmp_path: Path) -> None:
    store = tmp_path / "releases"
    for index in range(5):
        release = store / (f"{index:x}" * 40)
        release.mkdir(parents=True)
        (release / "payload").write_bytes(b"x" * 32)
    (store / ".staging-inflight").mkdir()

    result = _run_probe(store)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "generations=5" in result.stdout
    assert "bytes=" in result.stdout


def test_release_store_probe_fails_when_generation_bound_is_exceeded(tmp_path: Path) -> None:
    store = tmp_path / "releases"
    for index in range(7):
        (store / (f"{index:x}" * 40)).mkdir(parents=True)

    result = _run_probe(store)

    assert result.returncode != 0
    assert "generations=7" in result.stdout


def test_release_store_probe_fails_when_disk_bound_is_exceeded(tmp_path: Path) -> None:
    release = tmp_path / "releases" / _SHA
    release.mkdir(parents=True)
    (release / "payload").write_bytes(b"x" * 32)

    result = _run_probe(release.parent, max_bytes=1)

    assert result.returncode != 0
    assert "bytes=" in result.stdout


def test_healthcheck_registers_release_store_probe_as_local() -> None:
    body = _HEALTHCHECK.read_text(encoding="utf-8")

    assert "source \"$(dirname \"${BASH_SOURCE[0]}\")/release_store_probe.sh\"" in body
    assert "|release_store_usage|" in body
    assert "LOCAL_PROBES=" in body and "release_store_usage" in body
    assert "release_store_usage) probe_release_store_usage" in body
